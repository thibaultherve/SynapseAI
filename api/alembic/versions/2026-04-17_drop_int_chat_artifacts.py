"""Step 3/3 of chat_session int->UUID migration: drop artifacts + rename index.

Reversible housekeeping: drops the orphan SERIAL sequence from the old int PK
and renames the composite index to match the naming convention.

Revision ID: 009
Revises: 008
Create Date: 2026-04-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: str = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The SERIAL sequence from the old int PK. CASCADE dropping the column
    # in step 2 usually drops it, but older PG versions / manual creation
    # may leave it dangling.
    op.execute("DROP SEQUENCE IF EXISTS chat_session_id_seq")

    op.execute(
        "ALTER INDEX IF EXISTS ix_chat_message_uuid_session_id_created_at "
        "RENAME TO ix_chat_message_session_id_created_at"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX IF EXISTS ix_chat_message_session_id_created_at "
        "RENAME TO ix_chat_message_uuid_session_id_created_at"
    )
    # Sequence is not recreated — it was only meaningful with the old int PK,
    # which is restored separately by the 008 downgrade.
