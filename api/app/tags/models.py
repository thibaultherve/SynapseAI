from datetime import datetime

from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class Tag(Base):
    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("name", "category"),
        CheckConstraint(
            "category IN ('sub_domain', 'technique', 'pathology', 'topic')",
            name="valid_category",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
    )
