import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class Insight(Base):
    __tablename__ = "insight"
    __table_args__ = (
        CheckConstraint(
            "type IN ('trend', 'gap', 'hypothesis', 'methodology', "
            "'contradiction', 'opportunity')",
            name="valid_type",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="valid_confidence",
        ),
        CheckConstraint(
            "rating IN (1, -1)",
            name="valid_rating",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(10))
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class InsightPaper(Base):
    __tablename__ = "insight_paper"

    insight_id: Mapped[int] = mapped_column(
        ForeignKey("insight.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True
    )
