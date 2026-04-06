"""Add index on paper.publication_date for date-range filtering

Revision ID: 004a
Revises: 003b
Create Date: 2026-04-06

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004a"
down_revision: str = "003b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_paper_pub_date", "paper", ["publication_date"])


def downgrade() -> None:
    op.drop_index("idx_paper_pub_date", table_name="paper")
