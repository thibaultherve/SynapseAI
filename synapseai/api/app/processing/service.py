import asyncio
import logging
import uuid
from datetime import UTC, datetime

from app.config import processing_settings
from app.core.database import async_session
from app.core.enums import PaperStatus, SourceType
from app.papers.models import Paper
from app.processing.claude_service import generate_summaries
from app.processing.events import notify_paper_update
from app.processing.models import ProcessingEvent
from app.utils.text_extraction import extract_pdf_text, extract_web_text
from app.utils.url_validator import fetch_url_content

logger = logging.getLogger(__name__)

_processing_semaphore = asyncio.Semaphore(processing_settings.MAX_CONCURRENT_PROCESSING)


async def _log_event(db, paper_id: uuid.UUID, step: str, detail: str):
    event = ProcessingEvent(paper_id=paper_id, step=step, detail=detail)
    db.add(event)
    await db.commit()
    notify_paper_update(str(paper_id))


async def _update_status(db, paper: Paper, status: PaperStatus):
    paper.status = status.value
    await db.commit()
    notify_paper_update(str(paper.id))


async def process_paper(paper_id: uuid.UUID):
    """Background task: process a paper through the pipeline."""
    async with _processing_semaphore, async_session() as db:
        try:
            paper = await db.get(Paper, paper_id)
            if not paper:
                logger.error("Paper %s not found", paper_id)
                return

            # Step 1: Download (URL/DOI only) + Extract
            if not paper.extracted_text:
                if paper.source_type == SourceType.WEB and paper.url:
                    await _log_event(
                        db, paper_id, "downloading", "Downloading content from URL..."
                    )
                    await _update_status(db, paper, PaperStatus.UPLOADING)
                    content = await fetch_url_content(paper.url)
                    html_content = content.decode("utf-8", errors="replace")

                    await _log_event(
                        db, paper_id, "extracting", "Extracting text from web page..."
                    )
                    await _update_status(db, paper, PaperStatus.EXTRACTING)
                    paper.extracted_text = await extract_web_text(html_content)

                elif paper.source_type == SourceType.PDF and paper.file_path:
                    await _log_event(
                        db, paper_id, "extracting", "Extracting text from PDF..."
                    )
                    await _update_status(db, paper, PaperStatus.EXTRACTING)
                    paper.extracted_text = await extract_pdf_text(paper.file_path)

                paper.word_count = (
                    len(paper.extracted_text.split()) if paper.extracted_text else 0
                )
                await db.commit()

            if not paper.extracted_text:
                raise ValueError("No text content could be extracted")

            # Step 2: Summarize (skip if short_summary exists)
            if not paper.short_summary:
                await _log_event(
                    db, paper_id, "summarizing", "Generating summaries with Claude..."
                )
                await _update_status(db, paper, PaperStatus.SUMMARIZING)
                summaries = await generate_summaries(paper.extracted_text)

                # Apply metadata (only if not already set)
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
                await db.commit()

            # Done (Sprint 2 terminal)
            paper.processed_at = datetime.now(UTC).replace(tzinfo=None)
            await _update_status(db, paper, PaperStatus.SUMMARIZED)
            await _log_event(db, paper_id, "summarized", "Processing complete")

        except Exception as e:
            logger.exception("Processing failed for paper %s", paper_id)
            async with async_session() as err_db:
                paper = await err_db.get(Paper, paper_id)
                if paper:
                    paper.status = PaperStatus.ERROR.value
                    paper.error_message = str(e)[:1000]
                    await err_db.commit()
                    notify_paper_update(str(paper_id))
