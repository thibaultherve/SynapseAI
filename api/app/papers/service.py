import uuid
from datetime import date
from pathlib import Path

import aiofiles
from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import upload_settings
from app.core.enums import DerivedPaperStatus, SourceType, StepName
from app.core.exceptions import ConflictError
from app.papers.constants import ErrorCode
from app.papers.models import Paper, PaperTag
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


def _step_exists(status_val: str, step_name: str | None = None):
    """Build an EXISTS subquery on paper_step for a given status (and optional step name)."""
    clause = and_(PaperStep.paper_id == Paper.id, PaperStep.status == status_val)
    if step_name:
        clause = and_(clause, PaperStep.step == step_name)
    return exists(select(PaperStep.paper_id).where(clause))


def _apply_state_filter(query, state: DerivedPaperStatus):
    """Translate DerivedPaperStatus into SQL WHERE clauses on paper_step.

    Mirrors the priority logic in compute_paper_status():
      error > processing > enriched > readable > pending
    """
    has_error = _step_exists("error")
    has_processing = _step_exists("processing")

    if state == DerivedPaperStatus.ERROR:
        return query.where(has_error)

    if state == DerivedPaperStatus.PROCESSING:
        return query.where(~has_error, has_processing)

    # "enriched" = all non-crossrefing steps are done (and no error/processing)
    has_non_done_non_crossref = exists(
        select(PaperStep.paper_id).where(
            PaperStep.paper_id == Paper.id,
            PaperStep.step != StepName.CROSSREFING.value,
            PaperStep.status != "done",
        )
    )
    if state == DerivedPaperStatus.ENRICHED:
        return query.where(~has_error, ~has_processing, ~has_non_done_non_crossref)

    if state == DerivedPaperStatus.READABLE:
        has_summarized = _step_exists("done", StepName.SUMMARIZING.value)
        return query.where(
            ~has_error, ~has_processing, has_non_done_non_crossref, has_summarized
        )

    # PENDING: no error, no processing, not enriched, not readable
    if state == DerivedPaperStatus.PENDING:
        has_summarized = _step_exists("done", StepName.SUMMARIZING.value)
        return query.where(
            ~has_error, ~has_processing, has_non_done_non_crossref, ~has_summarized
        )

    return query


async def list_papers(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    tags: list[int] | None = None,
    state: DerivedPaperStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
) -> list[Paper]:
    query = select(Paper)

    if tags:
        query = query.join(PaperTag, Paper.id == PaperTag.paper_id).where(
            PaperTag.tag_id.in_(tags)
        ).distinct()

    if date_from:
        query = query.where(Paper.publication_date >= date_from)
    if date_to:
        query = query.where(Paper.publication_date <= date_to)

    if q:
        query = query.where(
            Paper.search_vector.op("@@")(func.websearch_to_tsquery("english", q))
        )

    if state:
        query = _apply_state_filter(query, state)

    result = await db.execute(
        query.order_by(Paper.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().unique().all())


async def delete_paper(paper: Paper, db: AsyncSession) -> None:
    await db.delete(paper)


async def update_paper(paper: Paper, update: PaperUpdate, db: AsyncSession) -> Paper:
    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(paper, field, value)
    return paper
