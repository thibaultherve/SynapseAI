"""Add paper_step table + backfill from existing paper.status

Revision ID: 003a
Revises: 002
Create Date: 2026-04-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "003a"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL_STEPS = ["uploading", "extracting", "summarizing", "tagging", "embedding", "crossrefing"]


def upgrade() -> None:
    # Create paper_step table
    op.create_table(
        "paper_step",
        sa.Column("paper_id", sa.Uuid(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("paper_id", "step"),
        sa.CheckConstraint(
            "step IN ('uploading', 'extracting', 'summarizing', "
            "'tagging', 'embedding', 'crossrefing')",
            name="valid_step_name",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'error', 'skipped')",
            name="valid_step_status",
        ),
    )

    # Indexes
    op.create_index("idx_paper_step_status", "paper_step", ["paper_id", "status"])
    op.create_index(
        "idx_paper_step_errors", "paper_step", ["paper_id"],
        postgresql_where=text("status = 'error'"),
    )
    op.create_index(
        "idx_paper_step_processing", "paper_step", ["paper_id"],
        postgresql_where=text("status = 'processing'"),
    )

    # Backfill: create 6 rows per existing paper based on current paper.status
    conn = op.get_bind()
    papers = conn.execute(text(
        "SELECT id, status, extracted_text, short_summary FROM paper"
    ))

    for paper in papers:
        steps = {}

        # Determine done steps from data presence
        if paper.extracted_text:
            steps["uploading"] = "done"
            steps["extracting"] = "done"
        elif paper.status == "extracting":
            steps["uploading"] = "done"
            steps["extracting"] = "processing"
        elif paper.status == "uploading":
            steps["uploading"] = "processing"
        else:
            steps["uploading"] = "done"

        if paper.short_summary:
            steps["summarizing"] = "done"
        elif paper.status == "summarizing":
            steps["summarizing"] = "processing"

        # Error state inference
        if paper.status == "error":
            if not paper.extracted_text:
                steps.setdefault("extracting", "error")
                steps.setdefault("uploading", "done")
            elif not paper.short_summary:
                steps.setdefault("summarizing", "error")
            steps.setdefault("uploading", "done")
            steps.setdefault("extracting", "done")

        # Summarized state
        if paper.status == "summarized":
            steps["uploading"] = "done"
            steps["extracting"] = "done"
            steps["summarizing"] = "done"

        # Fill remaining steps as pending
        for step in ALL_STEPS:
            steps.setdefault(step, "pending")

        # Insert
        for step, status in steps.items():
            conn.execute(text(
                "INSERT INTO paper_step (paper_id, step, status) "
                "VALUES (:pid, :step, :status)"
            ), {"pid": paper.id, "step": step, "status": status})


def downgrade() -> None:
    op.drop_table("paper_step")
