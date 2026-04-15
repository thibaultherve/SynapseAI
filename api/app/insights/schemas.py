from datetime import datetime
from typing import Literal

from pydantic import Field

from app.core.enums import InsightConfidence, InsightType
from app.core.schemas import AppBaseModel
from app.papers.schemas import PaperSummaryResponse


class InsightResponse(AppBaseModel):
    id: int
    type: InsightType
    title: str
    content: str
    evidence: str | None = None
    confidence: InsightConfidence
    rating: Literal[1, -1] | None = None
    supporting_papers: list[PaperSummaryResponse] = []
    detected_at: datetime
    updated_at: datetime


class InsightRatingRequest(AppBaseModel):
    rating: Literal[1, -1] | None = Field(
        default=None,
        description="User rating: 1=positive, -1=negative, null to clear",
    )


class InsightFilters(AppBaseModel):
    type: InsightType | None = None
    confidence: InsightConfidence | None = None
    rating: Literal[1, -1] | None = None
    limit: int = 50
    offset: int = 0


class InsightRefreshResponse(AppBaseModel):
    status: Literal["generated", "skipped"]
    hash: str
    insights_new: int = 0
    insights_merged: int = 0
    skipped: bool = False
