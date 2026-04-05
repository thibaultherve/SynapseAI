import uuid
from pathlib import Path

import aiofiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import upload_settings
from app.core.enums import SourceType, StepName
from app.core.exceptions import ConflictError
from app.papers.constants import ErrorCode
from app.papers.models import Paper
from app.papers.schemas import PaperUpdate
from app.processing.models import PaperStep
from app.utils.doi_resolver import resolve_doi
from app.utils.url_validator import validate_url


async def _create_initial_steps(db: AsyncSession, paper_id: uuid.UUID):
    """Create 6 paper_step rows (all pending) for a new paper."""
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))


async def create_paper_from_pdf(
    file_content: bytes, db: AsyncSession
) -> Paper:
    paper_id = uuid.uuid4()
    file_path = Path(upload_settings.UPLOAD_DIR) / f"{paper_id}.pdf"

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(file_content)

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        file_path=str(file_path),
    )
    db.add(paper)
    await db.flush()

    await _create_initial_steps(db, paper_id)
    await db.flush()
    await db.refresh(paper, ["steps"])

    # Import here to avoid circular imports
    from app.processing.service import process_paper
    from app.processing.task_registry import launch_processing

    launch_processing(process_paper(paper_id))
    return paper


async def create_paper_from_url(url: str, db: AsyncSession) -> Paper:
    await validate_url(url)

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB.value,
        url=url,
    )
    db.add(paper)
    await db.flush()

    await _create_initial_steps(db, paper_id)
    await db.flush()
    await db.refresh(paper, ["steps"])

    from app.processing.service import process_paper
    from app.processing.task_registry import launch_processing

    launch_processing(process_paper(paper_id))
    return paper


async def create_paper_from_doi(doi: str, db: AsyncSession) -> Paper:
    # Check for duplicate DOI
    existing = await db.execute(select(Paper).where(Paper.doi == doi))
    if existing.scalar_one_or_none():
        raise ConflictError(ErrorCode.DUPLICATE_DOI, "A paper with this DOI already exists")

    resolved_url = await resolve_doi(doi)

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB.value,
        doi=doi,
        url=resolved_url,
    )
    db.add(paper)
    await db.flush()

    await _create_initial_steps(db, paper_id)
    await db.flush()
    await db.refresh(paper, ["steps"])

    from app.processing.service import process_paper
    from app.processing.task_registry import launch_processing

    launch_processing(process_paper(paper_id))
    return paper


async def list_papers(db: AsyncSession, *, skip: int = 0, limit: int = 50) -> list[Paper]:
    result = await db.execute(
        select(Paper).order_by(Paper.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())


async def delete_paper(paper: Paper, db: AsyncSession) -> None:
    await db.delete(paper)


async def update_paper(paper: Paper, update: PaperUpdate, db: AsyncSession) -> Paper:
    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(paper, field, value)
    return paper
