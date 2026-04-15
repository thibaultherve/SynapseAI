"""Add pg_trgm + title_normalized for insight dedup SQL bascule

Revision ID: 006
Revises: 005
Create Date: 2026-04-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: str = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column(
        "insight",
        sa.Column("title_normalized", sa.String(length=255), nullable=True),
    )

    op.execute(
        """
        UPDATE insight
        SET title_normalized = lower(regexp_replace(title, '\\s+', ' ', 'g'))
        WHERE title_normalized IS NULL
        """
    )

    op.create_index(
        "idx_insight_title_trgm",
        "insight",
        ["title_normalized"],
        postgresql_using="gin",
        postgresql_ops={"title_normalized": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("idx_insight_title_trgm", table_name="insight")
    op.drop_column("insight", "title_normalized")
