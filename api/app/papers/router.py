from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import upload_settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.schemas import ErrorResponse
from app.papers import service
from app.papers.constants import ErrorCode
from app.papers.dependencies import get_paper_or_404, validate_upload
from app.papers.models import Paper
from app.papers.schemas import (
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
    description="List all papers ordered by creation date (descending).",
)
async def list_papers(
    skip: int = Query(0, ge=0, description="Number of papers to skip"),
    limit: int = Query(50, ge=1, le=100, description="Max papers to return"),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_papers(db, skip=skip, limit=limit)


@router.get(
    "/{paper_id}",
    response_model=PaperResponse,
    description="Get complete paper details including extracted text and summaries.",
    responses={404: {"model": ErrorResponse, "description": "Paper not found"}},
)
async def get_paper(paper: Paper = Depends(get_paper_or_404)):
    return paper


@router.get(
    "/{paper_id}/file",
    status_code=200,
    description="Download the original uploaded PDF file.",
    responses={
        404: {"model": ErrorResponse, "description": "Paper or file not found"},
    },
)
async def get_paper_file(paper: Paper = Depends(get_paper_or_404)):
    if not paper.file_path:
        raise NotFoundError(ErrorCode.NO_FILE, "No file associated with this paper")

    # Path traversal validation
    file_path = Path(paper.file_path).resolve()
    upload_dir = Path(upload_settings.UPLOAD_DIR).resolve()
    if not str(file_path).startswith(str(upload_dir)):
        raise NotFoundError(ErrorCode.NO_FILE, "File not found")

    if not file_path.exists():
        raise NotFoundError(ErrorCode.NO_FILE, "File not found on disk")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=f"{paper.id}.pdf",
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
