import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from app.config import embedding_settings, processing_settings
from app.core.database import async_session
from app.core.enums import SourceType, StepName, StepStatus
from app.papers.models import Paper
from app.processing.claude_service import generate_summaries, generate_tags
from app.processing.crossref_service import run_crossref_step
from app.processing.embedding_service import encode_batch
from app.processing.events import notify_paper_update
from app.processing.models import PaperEmbedding, PaperStep, ProcessingEvent
from app.tags.models import Tag
from app.tags.service import link_tags_to_paper, resolve_tags
from app.utils.chunking import chunk_text
from app.utils.text_extraction import extract_pdf_text, extract_web_text
from app.utils.url_validator import fetch_url_content

logger = logging.getLogger(__name__)

_processing_semaphore = asyncio.Semaphore(processing_settings.MAX_CONCURRENT_PROCESSING)
_embedding_semaphore = asyncio.Semaphore(1)


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def _get_step(paper: Paper, step_name: StepName) -> PaperStep:
    for s in paper.steps:
        if s.step == step_name.value:
            return s
    raise ValueError(f"Step '{step_name.value}' not found for paper {paper.id}")


def _mark_processing(step: PaperStep):
    step.status = StepStatus.PROCESSING.value
    step.started_at = datetime.now(UTC).replace(tzinfo=None)
    step.error_message = None
    step.completed_at = None


def _mark_done(step: PaperStep):
    step.status = StepStatus.DONE.value
    step.completed_at = datetime.now(UTC).replace(tzinfo=None)


def _mark_error(step: PaperStep, message: str):
    step.status = StepStatus.ERROR.value
    step.error_message = message[:1000]
    step.completed_at = datetime.now(UTC).replace(tzinfo=None)


async def _log_event(db, paper_id: uuid.UUID, step: str, detail: str):
    event = ProcessingEvent(paper_id=paper_id, step=step, detail=detail)
    db.add(event)
    await db.commit()
    notify_paper_update(str(paper_id))


async def _ensure_steps(db, paper: Paper):
    """Create 6 paper_step rows if they don't exist yet."""
    if not paper.steps:
        for step_name in StepName:
            db.add(PaperStep(paper_id=paper.id, step=step_name.value))
        await db.flush()
        await db.refresh(paper, ["steps"])


# ---------------------------------------------------------------------------
# can_retry — precondition checks for step retry
# ---------------------------------------------------------------------------

def can_retry(paper: Paper, step_name: str) -> tuple[bool, str]:
    """Check prerequisites for retrying a step. Returns (ok, reason)."""
    preconditions = {
        StepName.UPLOADING.value: lambda p: True,
        StepName.EXTRACTING.value: lambda p: bool(p.url or p.file_path),
        StepName.SUMMARIZING.value: lambda p: bool(p.extracted_text),
        StepName.TAGGING.value: lambda p: bool(p.short_summary),
        StepName.EMBEDDING.value: lambda p: bool(p.extracted_text),
        StepName.CROSSREFING.value: lambda p: bool(p.extracted_text),
    }

    check = preconditions.get(step_name)
    if check is None:
        return False, f"Unknown step: {step_name}"

    if not check(paper):
        return False, f"Prerequisites not met for retrying '{step_name}'"

    return True, ""


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------


async def _generate_embeddings(db, paper: Paper) -> None:
    """Chunk extracted text, encode via embedding model, and store in paper_embedding."""
    text_content = paper.extracted_text
    if not text_content:
        raise ValueError("No extracted text available for embedding generation")

    chunks = chunk_text(text_content)
    if not chunks:
        raise ValueError("Text chunking produced no chunks")

    batch_size = embedding_settings.EMBEDDING_BATCH_SIZE
    for batch_start in range(0, len(chunks), batch_size):
        batch_texts = chunks[batch_start : batch_start + batch_size]
        vectors = await encode_batch(batch_texts)

        values = [
            {
                "paper_id": paper.id,
                "chunk_index": batch_start + i,
                "chunk_text": batch_texts[i],
                "embedding": vectors[i],
            }
            for i in range(len(batch_texts))
        ]
        stmt = (
            pg_insert(PaperEmbedding)
            .values(values)
            .on_conflict_do_nothing(index_elements=["paper_id", "chunk_index"])
        )
        await db.execute(stmt)
        await db.flush()


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------

async def process_paper(paper_id: uuid.UUID):
    """Background task: process a paper through the pipeline."""
    async with _processing_semaphore, async_session() as db:
        paper = await db.get(
            Paper, paper_id, options=[selectinload(Paper.steps)]
        )
        if not paper:
            logger.error("Paper %s not found", paper_id)
            return

        await _ensure_steps(db, paper)

        current_step_name: str | None = None
        try:
            # Step 1: Upload / Download
            uploading = _get_step(paper, StepName.UPLOADING)
            web_content: bytes | None = None
            if uploading.status != StepStatus.DONE:
                current_step_name = StepName.UPLOADING.value
                _mark_processing(uploading)
                await db.commit()
                notify_paper_update(str(paper_id))
                await _log_event(db, paper_id, "uploading", "Preparing content...")

                if paper.source_type == SourceType.WEB and paper.url:
                    web_content = await fetch_url_content(paper.url)

                _mark_done(uploading)
                await db.commit()
                notify_paper_update(str(paper_id))

            # Step 2: Extract text
            extracting = _get_step(paper, StepName.EXTRACTING)
            if extracting.status != StepStatus.DONE:
                current_step_name = StepName.EXTRACTING.value
                _mark_processing(extracting)
                await db.commit()
                notify_paper_update(str(paper_id))
                await _log_event(db, paper_id, "extracting", "Extracting text...")

                if paper.source_type == SourceType.WEB:
                    if web_content is None and paper.url:
                        web_content = await fetch_url_content(paper.url)
                    html = (
                        web_content.decode("utf-8", errors="replace")
                        if web_content
                        else ""
                    )
                    paper.extracted_text = await extract_web_text(html)
                elif paper.source_type == SourceType.PDF and paper.file_path:
                    paper.extracted_text = await extract_pdf_text(paper.file_path)

                paper.word_count = (
                    len(paper.extracted_text.split()) if paper.extracted_text else 0
                )

                if not paper.extracted_text:
                    raise ValueError("No text content could be extracted")

                _mark_done(extracting)
                await db.commit()
                notify_paper_update(str(paper_id))

            # Step 3: Summarize
            summarizing = _get_step(paper, StepName.SUMMARIZING)
            if summarizing.status != StepStatus.DONE:
                current_step_name = StepName.SUMMARIZING.value
                _mark_processing(summarizing)
                await db.commit()
                notify_paper_update(str(paper_id))
                await _log_event(
                    db, paper_id, "summarizing", "Generating summaries with Claude..."
                )

                summaries = await generate_summaries(paper.extracted_text)

                if not paper.title:
                    paper.title = summaries.title
                if not paper.authors:
                    paper.authors = summaries.authors
                if not paper.authors_short:
                    paper.authors_short = summaries.authors_short
                if not paper.doi and summaries.doi:
                    paper.doi = summaries.doi
                if not paper.journal:
                    paper.journal = summaries.journal

                paper.short_summary = summaries.short_summary
                paper.detailed_summary = summaries.detailed_summary
                paper.key_findings = summaries.key_findings
                paper.keywords = summaries.keywords

                _mark_done(summarizing)
                await db.commit()
                notify_paper_update(str(paper_id))

            # Step 4: Tagging
            tagging = _get_step(paper, StepName.TAGGING)
            if tagging.status != StepStatus.DONE:
                current_step_name = StepName.TAGGING.value
                _mark_processing(tagging)
                await db.commit()
                notify_paper_update(str(paper_id))
                await _log_event(
                    db, paper_id, "tagging", "Assigning tags with Claude..."
                )

                # Build existing tags JSON for the prompt
                tag_result = await db.execute(
                    select(Tag).order_by(Tag.category, Tag.name)
                )
                all_tags = list(tag_result.scalars().all())
                existing_tags_json = json.dumps([
                    {"id": t.id, "name": t.name, "category": t.category}
                    for t in all_tags
                ])
                existing_tag_ids = {t.id for t in all_tags}

                tag_entries = await generate_tags(
                    paper.extracted_text,
                    paper.short_summary,
                    existing_tags_json,
                    existing_tag_ids,
                )

                tags = await resolve_tags(db, tag_entries)
                await link_tags_to_paper(db, paper.id, tags)

                _mark_done(tagging)
                await db.commit()
                notify_paper_update(str(paper_id))

            # Step 5: Embedding
            embedding = _get_step(paper, StepName.EMBEDDING)
            if embedding.status != StepStatus.DONE:
                current_step_name = StepName.EMBEDDING.value
                _mark_processing(embedding)
                await db.commit()
                notify_paper_update(str(paper_id))
                await _log_event(
                    db, paper_id, "embedding", "Generating embeddings..."
                )

                async with _embedding_semaphore:
                    await _generate_embeddings(db, paper)

                _mark_done(embedding)
                await db.commit()
                notify_paper_update(str(paper_id))

            # Step 6: Cross-references
            crossrefing = _get_step(paper, StepName.CROSSREFING)
            if crossrefing.status != StepStatus.DONE:
                current_step_name = StepName.CROSSREFING.value
                _mark_processing(crossrefing)
                await db.commit()
                notify_paper_update(str(paper_id))
                await _log_event(
                    db, paper_id, "crossrefing", "Finding related papers..."
                )

                await run_crossref_step(db, paper)

                _mark_done(crossrefing)
                await db.commit()
                notify_paper_update(str(paper_id))

            # Terminal
            paper.processed_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()
            await _log_event(db, paper_id, "complete", "Processing complete")

        except Exception as e:
            logger.exception("Processing failed for paper %s", paper_id)
            async with async_session() as err_db:
                if current_step_name:
                    result = await err_db.execute(
                        select(PaperStep).where(
                            PaperStep.paper_id == paper_id,
                            PaperStep.step == current_step_name,
                        )
                    )
                    err_step = result.scalar_one_or_none()
                    if err_step:
                        _mark_error(err_step, str(e))
                        await err_db.commit()
                        notify_paper_update(str(paper_id))
                else:
                    logger.error(
                        "Processing error for paper %s before any step started: %s",
                        paper_id, e,
                    )
