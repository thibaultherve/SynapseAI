"""Step 1/3 of chat_session int->UUID migration: add UUID columns + backfill.

Non-breaking: old int columns remain, ORM unchanged after this step.

Revision ID: 007
Revises: 006
Create Date: 2026-04-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: str = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # chat_session.uuid_id: populated on every existing row via server default.
    op.add_column(
        "chat_session",
        sa.Column(
            "uuid_id",
            sa.Uuid,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_chat_session_uuid_id", "chat_session", ["uuid_id"]
    )

    # chat_message.uuid_session_id: nullable for backfill, NOT NULL after.
    op.add_column(
        "chat_message",
        sa.Column("uuid_session_id", sa.Uuid, nullable=True),
    )

    op.execute(
        """
        UPDATE chat_message AS cm
        SET uuid_session_id = cs.uuid_id
        FROM chat_session AS cs
        WHERE cm.session_id = cs.id
        """
    )

    op.alter_column("chat_message", "uuid_session_id", nullable=False)

    op.create_foreign_key(
        "fk_chat_message_uuid_session_id_chat_session",
        "chat_message",
        "chat_session",
        ["uuid_session_id"],
        ["uuid_id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_chat_message_uuid_session_id_created_at",
        "chat_message",
        ["uuid_session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_message_uuid_session_id_created_at", table_name="chat_message"
    )
    op.drop_constraint(
        "fk_chat_message_uuid_session_id_chat_session",
        "chat_message",
        type_="foreignkey",
    )
    op.drop_column("chat_message", "uuid_session_id")
    op.drop_constraint(
        "uq_chat_session_uuid_id", "chat_session", type_="unique"
    )
    op.drop_column("chat_session", "uuid_id")
