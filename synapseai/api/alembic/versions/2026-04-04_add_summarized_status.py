"""Add 'summarized' to paper status CHECK constraint

Revision ID: 002
Revises: 001
Create Date: 2026-04-04

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_paper_valid_status", "paper", type_="check")
    op.create_check_constraint(
        "ck_paper_valid_status",
        "paper",
        "status IN ('uploading', 'extracting', 'summarizing', 'summarized', "
        "'tagging', 'embedding', 'crossrefing', 'done', 'error', 'deleted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_paper_valid_status", "paper", type_="check")
    op.create_check_constraint(
        "ck_paper_valid_status",
        "paper",
        "status IN ('uploading', 'extracting', 'summarizing', "
        "'tagging', 'embedding', 'crossrefing', 'done', 'error', 'deleted')",
    )
