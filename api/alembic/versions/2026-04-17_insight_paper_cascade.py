"""Re-assert ON DELETE CASCADE on insight_paper foreign keys.

The initial schema and ORM model both declare CASCADE, but earlier inline FK
declarations in `op.create_table` bypassed the metadata naming convention and
left PostgreSQL-default constraint names (`insight_paper_*_fkey`). This
migration normalizes the names to the project convention and guarantees the
CASCADE semantics are applied even if an environment drifted.

Idempotent: drops any existing FK on the two columns (either naming), then
recreates with the convention names and explicit ON DELETE CASCADE.

Revision ID: 010
Revises: 009
Create Date: 2026-04-17

"""
from typing import Sequence, Union

from alembic import op

revision: str = "010"
down_revision: str = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop under both possible prior names (PG default and convention).
    op.execute(
        "ALTER TABLE insight_paper "
        "DROP CONSTRAINT IF EXISTS insight_paper_insight_id_fkey"
    )
    op.execute(
        "ALTER TABLE insight_paper "
        "DROP CONSTRAINT IF EXISTS fk_insight_paper_insight_id_insight"
    )
    op.execute(
        "ALTER TABLE insight_paper "
        "DROP CONSTRAINT IF EXISTS insight_paper_paper_id_fkey"
    )
    op.execute(
        "ALTER TABLE insight_paper "
        "DROP CONSTRAINT IF EXISTS fk_insight_paper_paper_id_paper"
    )

    op.create_foreign_key(
        "fk_insight_paper_insight_id_insight",
        "insight_paper",
        "insight",
        ["insight_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_insight_paper_paper_id_paper",
        "insight_paper",
        "paper",
        ["paper_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Keep CASCADE (the prior state also had CASCADE); just restore the
    # PG-default constraint names so downgrade is a pure rename.
    op.execute(
        "ALTER TABLE insight_paper "
        "DROP CONSTRAINT IF EXISTS fk_insight_paper_insight_id_insight"
    )
    op.execute(
        "ALTER TABLE insight_paper "
        "DROP CONSTRAINT IF EXISTS fk_insight_paper_paper_id_paper"
    )
    op.execute(
        "ALTER TABLE insight_paper "
        "ADD CONSTRAINT insight_paper_insight_id_fkey "
        "FOREIGN KEY (insight_id) REFERENCES insight(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE insight_paper "
        "ADD CONSTRAINT insight_paper_paper_id_fkey "
        "FOREIGN KEY (paper_id) REFERENCES paper(id) ON DELETE CASCADE"
    )
