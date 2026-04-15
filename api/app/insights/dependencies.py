from typing import Literal

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import InsightConfidence, InsightType
from app.insights.exceptions import InsightNotFoundError
from app.insights.models import Insight
from app.insights.schemas import InsightFilters


async def get_insight_or_404(
    insight_id: int,
    db: AsyncSession = Depends(get_db),
) -> Insight:
    insight = await db.get(Insight, insight_id)
    if not insight:
        raise InsightNotFoundError(insight_id)
    return insight


async def get_insight_filters(
    type: InsightType | None = Query(None, description="Filter by insight type"),
    confidence: InsightConfidence | None = Query(
        None, description="Filter by confidence level"
    ),
    rating: Literal[1, -1] | None = Query(
        None,
        description="Filter by user rating (1=positive, -1=negative)",
    ),
    limit: int = Query(50, ge=1, le=200, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Items to skip"),
) -> InsightFilters:
    return InsightFilters(
        type=type,
        confidence=confidence,
        rating=rating,
        limit=limit,
        offset=offset,
    )
