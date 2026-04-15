"""Add index on chat_message.session_id for session-scoped lookups

Revision ID: 005
Revises: 004b
Create Date: 2026-04-14

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: str = "004b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_chat_message_session", "chat_message", ["session_id"])


def downgrade() -> None:
    op.drop_index("idx_chat_message_session", table_name="chat_message")
