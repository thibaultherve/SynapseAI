import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base

if TYPE_CHECKING:
    from app.processing.models import PaperStep
    from app.tags.models import Tag


class Paper(Base):
    __tablename__ = "paper"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('pdf', 'web')",
            name="valid_source_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    authors_short: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[date | None] = mapped_column(Date)
    journal: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text, unique=True)
    url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(10))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    short_summary: Mapped[str | None] = mapped_column(Text)
    detailed_summary: Mapped[str | None] = mapped_column(Text)
    key_findings: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    word_count: Mapped[int | None] = mapped_column(Integer)
    file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column()
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', "
            "coalesce(title, '') || ' ' || "
            "coalesce(short_summary, '') || ' ' || "
            "coalesce(extracted_text, ''))",
            persisted=True,
        ),
    )

    steps: Mapped[list["PaperStep"]] = relationship(
        back_populates="paper",
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary="paper_tag",
        lazy="selectin",
        passive_deletes=True,
    )


class PaperTag(Base):
    __tablename__ = "paper_tag"

    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True
    )
