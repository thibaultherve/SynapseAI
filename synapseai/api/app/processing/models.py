import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from pgvector.sqlalchemy import Vector

from app.core.base import Base


class PaperEmbedding(Base):
    __tablename__ = "paper_embedding"
    __table_args__ = (UniqueConstraint("paper_id", "chunk_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(384))


class CrossReference(Base):
    __tablename__ = "cross_reference"
    __table_args__ = (
        UniqueConstraint("paper_a", "paper_b"),
        CheckConstraint("paper_a < paper_b", name="ordered_pair"),
        CheckConstraint(
            "relation_type IN ('supports', 'contradicts', 'extends', "
            "'methodological', 'thematic')",
            name="valid_relation_type",
        ),
        CheckConstraint(
            "strength IN ('strong', 'moderate', 'weak')",
            name="valid_strength",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_a: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE")
    )
    paper_b: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE")
    )
    relation_type: Mapped[str] = mapped_column(String(20))
    strength: Mapped[str] = mapped_column(String(10))
    description: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProcessingEvent(Base):
    __tablename__ = "processing_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE")
    )
    step: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
