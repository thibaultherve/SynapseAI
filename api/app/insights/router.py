from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import insight_settings
from app.core.database import get_db
from app.core.schemas import ErrorResponse
from app.insights import service
from app.insights.debouncer import insight_debouncer
from app.insights.dependencies import get_insight_filters, get_insight_or_404
from app.insights.exceptions import InsightRefreshBusyError
from app.insights.models import Insight
from app.insights.schemas import (
    InsightFilters,
    InsightRatingRequest,
    InsightRefreshResponse,
    InsightResponse,
)
from app.ratelimit import limiter

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get(
    "",
    response_model=list[InsightResponse],
    status_code=status.HTTP_200_OK,
    summary="List insights",
    description=(
        "List insights with optional filters. Insights without supporting papers "
        "are excluded from the result."
    ),
    responses={429: {"description": "Rate limit exceeded"}},
)
@limiter.limit("60/minute")
async def list_insights(
    request: Request,
    db: AsyncSession = Depends(get_db),
    filters: InsightFilters = Depends(get_insight_filters),
):
    return await service.list_insights(db, filters)


@router.get(
    "/{insight_id}",
    response_model=InsightResponse,
    status_code=status.HTTP_200_OK,
    summary="Get insight detail",
    responses={
        404: {"model": ErrorResponse, "description": "Insight not found"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("60/minute")
async def get_insight(
    request: Request,
    insight: Insight = Depends(get_insight_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_insight(db, insight)


@router.patch(
    "/{insight_id}/rating",
    response_model=InsightResponse,
    status_code=status.HTTP_200_OK,
    summary="Rate an insight",
    description="Rate an insight with 1 (positive), -1 (negative), or null (clear).",
    responses={
        404: {"model": ErrorResponse, "description": "Insight not found"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/minute")
async def rate_insight(
    request: Request,
    payload: InsightRatingRequest,
    insight: Insight = Depends(get_insight_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_rating(db, insight, payload.rating)


@router.post(
    "/refresh",
    response_model=InsightRefreshResponse,
    status_code=status.HTTP_200_OK,
    summary="Manually trigger insight generation",
    description=(
        "Run insight generation immediately. Returns 409 if a generation is "
        "already in progress; returns 200 with `skipped: true` when the "
        "context hash matches the last successful run."
    ),
    responses={
        409: {"model": ErrorResponse, "description": "Refresh already in progress"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit(insight_settings.INSIGHT_REFRESH_RATE)
async def refresh_insights(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if insight_debouncer.is_locked():
        raise InsightRefreshBusyError()

    result = await insight_debouncer.run_now()
    # Lazy cleanup of orphan insights (spec §3.3).
    await service.cleanup_orphan_insights(db)
    return InsightRefreshResponse(**result)


@router.delete(
    "/{insight_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an insight",
    responses={
        204: {"description": "Insight deleted"},
        404: {"model": ErrorResponse, "description": "Insight not found"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("5/minute")
async def delete_insight(
    request: Request,
    insight: Insight = Depends(get_insight_or_404),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_insight(db, insight)
