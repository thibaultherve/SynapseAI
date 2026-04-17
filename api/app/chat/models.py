import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class ChatSession(Base):
    __tablename__ = "chat_session"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'paper' AND paper_id IS NOT NULL) OR (scope = 'corpus')",
            name="scope_paper_check",
        ),
        CheckConstraint(
            "scope IN ('paper', 'corpus')",
            name="valid_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    paper_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(10), server_default="paper")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="valid_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
