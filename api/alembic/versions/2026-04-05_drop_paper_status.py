"""Drop paper.status and paper.error_message columns

Revision ID: 003b
Revises: 003a
Create Date: 2026-04-05

IMPORTANT: Deploy code that reads paper_step BEFORE running this migration.
Migration 003a is safe to rollback. This migration (003b) is destructive.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003b"
down_revision: Union[str, None] = "003a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: op.drop_constraint applies the MetaData naming convention
    # (ck_%(table_name)s_%(constraint_name)s), so "valid_status" becomes
    # "ck_paper_valid_status" which is the actual DB constraint name.
    op.drop_constraint("valid_status", "paper", type_="check")

    # Drop the index on status before dropping the column (PG auto-drops
    # indexes when the column is removed, but being explicit is cleaner)
    op.drop_index("idx_paper_status", table_name="paper")

    # Drop columns
    op.drop_column("paper", "status")
    op.drop_column("paper", "error_message")


def downgrade() -> None:
    # Restore columns
    op.add_column("paper", sa.Column("status", sa.String(20), server_default="uploading", nullable=False))
    op.add_column("paper", sa.Column("error_message", sa.Text))

    # Restore CHECK constraint
    op.create_check_constraint(
        "ck_paper_valid_status",
        "paper",
        "status IN ('uploading', 'extracting', 'summarizing', 'summarized', "
        "'tagging', 'embedding', 'crossrefing', 'done', 'error', 'deleted')",
    )

    # Restore index
    op.create_index("idx_paper_status", "paper", ["status"])
