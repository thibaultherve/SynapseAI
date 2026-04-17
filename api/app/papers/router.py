from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import upload_settings
from app.core.database import get_db
from app.core.enums import DerivedPaperStatus, ReferenceStrength, RelationType
from app.core.exceptions import NotFoundError
from app.core.schemas import ErrorResponse
from app.papers import service
from app.papers.constants import ErrorCode
from app.papers.dependencies import get_paper_or_404, validate_upload
from app.papers.exceptions import PaperFileMissingError
from app.papers.models import Paper
from app.papers.schemas import (
    CrossrefResponse,
    PaperCreate,
    PaperResponse,
    PaperSummaryResponse,
    PaperUpdate,
)
from app.ratelimit import limiter

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.post(
    "/upload",
    response_model=PaperResponse,
    status_code=201,
    description="Upload a PDF file for processing.",
    responses={
        413: {"model": ErrorResponse, "description": "File exceeds 100MB"},
        422: {"model": ErrorResponse, "description": "Invalid file type"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("10/minute")
async def upload_paper(
    request: Request,
    file_content: bytes = Depends(validate_upload),
    db: AsyncSession = Depends(get_db),
):
    paper = await service.create_paper_from_pdf(file_content, db)
    return paper


@router.post(
    "",
    response_model=PaperResponse,
    status_code=201,
    description="Create a paper from a URL or DOI. Validates SSRF.",
    responses={
        409: {"model": ErrorResponse, "description": "Duplicate DOI"},
        422: {"model": ErrorResponse, "description": "Invalid URL/DOI"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/minute")
async def create_paper(
    request: Request,
    body: PaperCreate,
    db: AsyncSession = Depends(get_db),
):
    if body.doi:
        paper = await service.create_paper_from_doi(body.doi, db)
    else:
        paper = await service.create_paper_from_url(str(body.url), db)
    return paper


@router.get(
    "",
    response_model=list[PaperSummaryResponse],
    status_code=200,
    description="List papers with optional filters: tags, state, date range, full-text search.",
)
async def list_papers(
    skip: int = Query(0, ge=0, description="Number of papers to skip"),
    limit: int = Query(50, ge=1, le=100, description="Max papers to return"),
    tags: list[int] | None = Query(None, description="Filter by tag IDs (OR logic)"),
    state: DerivedPaperStatus | None = Query(None, description="Filter by derived state"),
    date_from: date | None = Query(None, description="Min publication date (inclusive)"),
    date_to: date | None = Query(None, description="Max publication date (inclusive)"),
    q: str | None = Query(None, min_length=1, max_length=200, description="Full-text search query"),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_papers(
        db,
        skip=skip,
        limit=limit,
        tags=tags,
        state=state,
        date_from=date_from,
        date_to=date_to,
        q=q,
    )


@router.get(
    "/{paper_id}",
    response_model=PaperResponse,
    description="Get complete paper details including extracted text and summaries.",
    responses={404: {"model": ErrorResponse, "description": "Paper not found"}},
)
async def get_paper(paper: Paper = Depends(get_paper_or_404)):
    return paper


@router.get(
    "/{paper_id}/crossrefs",
    response_model=list[CrossrefResponse],
    description="List detected cross-references for this paper (hydrated with the related paper).",
    responses={
        404: {"model": ErrorResponse, "description": "Paper not found"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("30/minute")
async def get_paper_crossrefs(
    request: Request,
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
    relation_type: RelationType | None = Query(
        None, description="Filter by relation type"
    ),
    min_strength: ReferenceStrength | None = Query(
        None, description="Minimum strength: weak | moderate | strong"
    ),
    limit: int = Query(20, ge=1, le=100),
):
    return await service.get_paper_crossrefs(
        db,
        paper.id,
        relation_type=relation_type.value if relation_type else None,
        min_strength=min_strength.value if min_strength else None,
        limit=limit,
    )


@router.get(
    "/{paper_id}/file",
    status_code=200,
    description="Download the original uploaded PDF file.",
    responses={
        404: {"model": ErrorResponse, "description": "Paper or file not found"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("30/minute")
async def get_paper_file(
    request: Request,
    paper: Paper = Depends(get_paper_or_404),
):
    if not paper.file_path:
        raise NotFoundError(ErrorCode.NO_FILE, "No file associated with this paper")

    try:
        upload_dir = Path(upload_settings.UPLOAD_DIR).resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise PaperFileMissingError()

    candidate = Path(paper.file_path)
    # Reject symlinks at the leaf explicitly: resolve() would silently
    # follow them, so a symlink planted inside UPLOAD_DIR pointing at
    # /etc/passwd would otherwise slip past is_relative_to.
    if candidate.is_symlink():
        raise PaperFileMissingError()

    try:
        real = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise PaperFileMissingError()

    if not real.is_relative_to(upload_dir):
        raise PaperFileMissingError()

    if not real.is_file():
        raise PaperFileMissingError()

    return FileResponse(
        path=str(real),
        media_type="application/pdf",
        filename=f"{paper.id}.pdf",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/{paper_id}",
    status_code=204,
    description="Delete a paper and all associated data (CASCADE).",
    responses={404: {"model": ErrorResponse, "description": "Paper not found"}},
)
async def delete_paper(
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_paper(paper, db)


@router.patch(
    "/{paper_id}",
    response_model=PaperResponse,
    description="Update paper metadata (title, authors, journal, DOI, URL).",
    responses={404: {"model": ErrorResponse, "description": "Paper not found"}},
)
async def update_paper(
    body: PaperUpdate,
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_paper(paper, body, db)
