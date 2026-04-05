from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.schemas import ErrorResponse
from app.papers.schemas import PaperSummaryResponse
from app.ratelimit import limiter
from app.tags import service
from app.tags.dependencies import get_tag_or_404
from app.tags.models import Tag
from app.tags.schemas import TagMergeRequest, TagResponse, TagUpdate

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get(
    "",
    response_model=dict[str, list[TagResponse]],
    description="List all tags grouped by category.",
)
@limiter.limit("60/minute")
async def list_tags(
    request: Request,
    category: str | None = Query(
        None,
        description="Filter by category (sub_domain, technique, pathology, topic)",
    ),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_all_tags(db, category=category)


@router.get(
    "/{tag_id}/papers",
    response_model=list[PaperSummaryResponse],
    description="List papers associated with a tag.",
    responses={404: {"model": ErrorResponse, "description": "Tag not found"}},
)
@limiter.limit("30/minute")
async def get_tag_papers(
    request: Request,
    tag: Tag = Depends(get_tag_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_tag_papers(db, tag.id)


@router.patch(
    "/{tag_id}",
    response_model=TagResponse,
    description="Rename a tag.",
    responses={
        404: {"model": ErrorResponse, "description": "Tag not found"},
        409: {"model": ErrorResponse, "description": "Duplicate tag name"},
    },
)
@limiter.limit("10/minute")
async def rename_tag(
    request: Request,
    body: TagUpdate,
    tag: Tag = Depends(get_tag_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await service.rename_tag(db, tag, body)


@router.delete(
    "/{tag_id}",
    status_code=204,
    description="Delete a tag (cascades to paper_tag associations).",
    responses={404: {"model": ErrorResponse, "description": "Tag not found"}},
)
@limiter.limit("5/minute")
async def delete_tag(
    request: Request,
    tag: Tag = Depends(get_tag_or_404),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_tag(db, tag)


@router.post(
    "/merge",
    response_model=TagResponse,
    description="Merge source tag into target tag. Moves all paper associations.",
    responses={
        404: {"model": ErrorResponse, "description": "Tag not found"},
        422: {"model": ErrorResponse, "description": "Invalid merge request"},
    },
)
@limiter.limit("5/minute")
async def merge_tags(
    request: Request,
    body: TagMergeRequest,
    db: AsyncSession = Depends(get_db),
):
    return await service.merge_tags(db, body.source_id, body.target_id)
