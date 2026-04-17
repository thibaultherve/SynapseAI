"""Step 2/3 of chat_session int->UUID migration: swap PK to UUID.

BREAKING: old int IDs are lost after this step. Clients that cached int
session IDs will 404. Ship this in the same release as the ORM switch
(Mapped[int] -> Mapped[uuid.UUID]) in app/chat/models.py and schemas.

Revision ID: 008
Revises: 007
Create Date: 2026-04-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: str = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the temp UUID FK added in step 1 — we'll recreate it under a
    # canonical name after columns are renamed.
    op.drop_constraint(
        "fk_chat_message_uuid_session_id_chat_session",
        "chat_message",
        type_="foreignkey",
    )

    # Drop the old int session_id column. Cascades its FK to chat_session.id
    # and the single-column index idx_chat_message_session (added in 005).
    op.execute("ALTER TABLE chat_message DROP COLUMN session_id CASCADE")

    # Promote the UUID column to the canonical name.
    op.alter_column(
        "chat_message", "uuid_session_id", new_column_name="session_id"
    )

    # Drop the int PK + column on chat_session. Drop the UNIQUE on uuid_id
    # (it becomes PK below).
    op.execute("ALTER TABLE chat_session DROP COLUMN id CASCADE")
    op.drop_constraint(
        "uq_chat_session_uuid_id", "chat_session", type_="unique"
    )
    op.alter_column("chat_session", "uuid_id", new_column_name="id")

    op.create_primary_key("pk_chat_session", "chat_session", ["id"])

    op.create_foreign_key(
        "fk_chat_message_session_id_chat_session",
        "chat_message",
        "chat_session",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Irreversible in practice: the old integer IDs are gone. This downgrade
    # restores the column shapes with fresh sequences, but any referential
    # link to pre-swap data is lost. Restore from backup if rollback is
    # required on populated data.
    op.drop_constraint(
        "fk_chat_message_session_id_chat_session",
        "chat_message",
        type_="foreignkey",
    )
    op.drop_constraint("pk_chat_session", "chat_session", type_="primary")

    op.alter_column("chat_session", "id", new_column_name="uuid_id")
    op.execute(
        "ALTER TABLE chat_session ADD COLUMN id SERIAL PRIMARY KEY"
    )
    op.create_unique_constraint(
        "uq_chat_session_uuid_id", "chat_session", ["uuid_id"]
    )

    op.alter_column(
        "chat_message", "session_id", new_column_name="uuid_session_id"
    )
    op.execute(
        "ALTER TABLE chat_message ADD COLUMN session_id INTEGER"
    )
    op.create_foreign_key(
        "fk_chat_message_uuid_session_id_chat_session",
        "chat_message",
        "chat_session",
        ["uuid_session_id"],
        ["uuid_id"],
        ondelete="CASCADE",
    )
