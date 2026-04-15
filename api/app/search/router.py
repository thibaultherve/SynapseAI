from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.schemas import ErrorResponse
from app.papers.dependencies import get_paper_or_404
from app.papers.models import Paper
from app.ratelimit import limiter
from app.search import service
from app.search.schemas import TOLERANCE_THRESHOLDS, SearchMode, SearchRequest, SearchResponse, SearchResultItem

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post(
    "",
    response_model=SearchResponse,
    status_code=200,
    description="Search papers using full-text search or semantic search.",
    responses={
        422: {"description": "Validation error"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("30/minute")
async def search_papers(
    request: Request,
    body: SearchRequest,
    db: AsyncSession = Depends(get_db),
):
    if body.mode == SearchMode.SEMANTIC:
        min_score = TOLERANCE_THRESHOLDS[body.tolerance]
        results, total = await service.semantic_search(
            db, body.query, body.limit, body.offset, body.filters, min_score
        )
    else:
        results, total = await service.full_text_search(
            db, body.query, body.limit, body.offset, body.filters
        )

    return SearchResponse(
        results=results,
        total_count=total,
        query=body.query,
        mode=body.mode,
    )


@router.get(
    "/similar/{paper_id}",
    response_model=list[SearchResultItem],
    status_code=200,
    description="Find papers similar to a given paper based on embedding similarity.",
    responses={404: {"model": ErrorResponse, "description": "Paper not found"}},
)
@limiter.limit("20/minute")
async def find_similar_papers(
    request: Request,
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await service.find_similar(db, paper.id)
