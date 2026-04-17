import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.core.database import async_session
from app.core.enums import SourceType
from app.papers.models import Paper, PaperTag
from app.processing.models import PaperEmbedding, PaperStep
from app.tags.models import Tag


@pytest.mark.asyncio
async def test_process_paper_not_found(db):
    """process_paper with nonexistent paper_id -> logs error and returns without crash."""
    from app.processing.service import process_paper

    fake_id = uuid.uuid4()
    await process_paper(fake_id)

    # No paper should exist, nothing to assert except no exception raised


@pytest.mark.asyncio
async def test_process_paper_no_text_extracted(db, tmp_upload_dir, mock_claude):
    """process_paper when extraction yields no text -> extracting step goes to error."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        file_path="nonexistent.pdf",
    )
    db.add(paper)
    await db.commit()

    with patch(
        "app.processing.service.extract_pdf_text",
        new_callable=AsyncMock,
        return_value="",
    ):
        await process_paper(paper_id)

    async with async_session() as verify_db:
        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s for s in steps}
        assert step_map["extracting"].status == "error"
        assert "No text content" in step_map["extracting"].error_message


@pytest.mark.asyncio
async def test_process_paper_web_pipeline(db, mock_claude, mock_embedding):
    """process_paper with web source -> downloads, extracts, summarizes, embeds."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB.value,
        url="https://example.com/paper",
    )
    db.add(paper)
    await db.commit()

    with (
        patch(
            "app.processing.service.fetch_url_content",
            new_callable=AsyncMock,
            return_value=b"<html><body>Some research content here</body></html>",
        ),
        patch(
            "app.processing.service.extract_web_text",
            new_callable=AsyncMock,
            return_value="Extracted web text from the research paper.",
        ),
        patch(
            "app.processing.service.generate_tags",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await process_paper(paper_id)

    async with async_session() as verify_db:
        result = await verify_db.get(Paper, paper_id)
        assert result.extracted_text == "Extracted web text from the research paper."
        assert result.short_summary is not None
        assert result.processed_at is not None

        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s.status for s in steps}
        assert step_map["uploading"] == "done"
        assert step_map["extracting"] == "done"
        assert step_map["summarizing"] == "done"
        assert step_map["tagging"] == "done"
        assert step_map["embedding"] == "done"


@pytest.mark.asyncio
async def test_process_paper_extraction_error_sets_error_state(db, tmp_upload_dir):
    """process_paper when extraction raises -> extracting step error with message."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        file_path="some.pdf",
    )
    db.add(paper)
    await db.commit()

    with patch(
        "app.processing.service.extract_pdf_text",
        new_callable=AsyncMock,
        side_effect=RuntimeError("PDF corrupted"),
    ):
        await process_paper(paper_id)

    async with async_session() as verify_db:
        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s for s in steps}
        # Uploading should be done (PDF file already uploaded)
        assert step_map["uploading"].status == "done"
        # Extracting should be in error
        assert step_map["extracting"].status == "error"
        assert "PDF corrupted" in step_map["extracting"].error_message


# --- T16: Pipeline tagging step ---


def _make_mock_claude_for_tagging():
    """Create a mock that returns summary for first call and tags for second call."""
    summary_json = json.dumps({
        "title": "Test Paper Title",
        "authors": ["Author One"],
        "authors_short": "One et al.",
        "publication_date": "2024-01-15",
        "journal": "Nature",
        "doi": None,
        "short_summary": "This paper investigates neural networks.",
        "detailed_summary": "Detailed summary of the paper.",
        "key_findings": "1. Finding one",
        "keywords": ["neural-network"],
    })

    tagging_json = json.dumps({
        "tags": [
            {"new": {"name": "Deep Learning", "category": "technique", "description": "DL methods"}},
            {"new": {"name": "Neuroscience", "category": "sub_domain", "description": None}},
        ]
    })

    call_count = 0

    async def mock_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_process = AsyncMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()
        mock_process.returncode = 0

        if call_count == 1:
            # First call: summarization
            mock_process.communicate.return_value = (
                json.dumps({"result": summary_json}).encode(), b""
            )
        else:
            # Second call: tagging
            mock_process.communicate.return_value = (
                json.dumps({"result": tagging_json}).encode(), b""
            )
        return mock_process

    return mock_subprocess


@pytest.mark.asyncio
async def test_process_paper_tagging_step(db, mock_embedding):
    """process_paper runs tagging step after summarizing, assigns tags to paper."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB.value,
        url="https://example.com/paper",
    )
    db.add(paper)
    await db.commit()

    with (
        patch(
            "app.processing.service.fetch_url_content",
            new_callable=AsyncMock,
            return_value=b"<html><body>Research content</body></html>",
        ),
        patch(
            "app.processing.service.extract_web_text",
            new_callable=AsyncMock,
            return_value="Extracted text about neural networks and neuroscience.",
        ),
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=_make_mock_claude_for_tagging(),
        ),
    ):
        await process_paper(paper_id)

    async with async_session() as verify_db:
        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s.status for s in steps}

        assert step_map["uploading"] == "done"
        assert step_map["extracting"] == "done"
        assert step_map["summarizing"] == "done"
        assert step_map["tagging"] == "done"

        # Verify tags were created
        tags = (await verify_db.execute(select(Tag))).scalars().all()
        tag_names = {t.name for t in tags}
        assert "Deep Learning" in tag_names
        assert "Neuroscience" in tag_names

        # Verify paper_tag associations
        paper_tags = (await verify_db.execute(
            select(PaperTag).where(PaperTag.paper_id == paper_id)
        )).scalars().all()
        assert len(paper_tags) == 2


# --- T24: Pipeline embedding step ---


@pytest.mark.asyncio
async def test_process_paper_embedding_step(db, mock_claude, mock_embedding):
    """process_paper runs embedding step: chunks text, encodes, stores embeddings."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB.value,
        url="https://example.com/paper",
    )
    db.add(paper)
    await db.commit()

    with (
        patch(
            "app.processing.service.fetch_url_content",
            new_callable=AsyncMock,
            return_value=b"<html><body>Research content</body></html>",
        ),
        patch(
            "app.processing.service.extract_web_text",
            new_callable=AsyncMock,
            return_value="This is a research paper about neuroscience. "
            "It covers many topics in depth. " * 20,
        ),
        patch(
            "app.processing.service.generate_tags",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await process_paper(paper_id)

    async with async_session() as verify_db:
        # Verify embedding step is done
        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s.status for s in steps}
        assert step_map["embedding"] == "done"

        # Verify embeddings were stored
        embeddings = (await verify_db.execute(
            select(PaperEmbedding).where(PaperEmbedding.paper_id == paper_id)
        )).scalars().all()
        assert len(embeddings) > 0
        # Each embedding should have a chunk_text and chunk_index
        for emb in embeddings:
            assert emb.chunk_text is not None
            assert emb.chunk_index >= 0


# --- Phase 3: event-bus decoupling (processing -> insights) ---


@pytest.mark.asyncio
async def test_pipeline_publishes_paper_processed_schedules_insights(
    db, tmp_upload_dir, mock_claude, mock_embedding, monkeypatch
):
    """End of pipeline publishes Event.PAPER_PROCESSED, debouncer handler calls schedule()."""
    from pathlib import Path

    from app.insights.debouncer import insight_debouncer
    from app.processing.service import process_paper

    schedule_mock = MagicMock()
    monkeypatch.setattr(insight_debouncer, "schedule", schedule_mock)
    insight_debouncer.start()

    paper_id = uuid.uuid4()
    file_path = Path(tmp_upload_dir) / f"{paper_id}.pdf"
    file_path.write_bytes(b"%PDF-1.4 fake pdf")
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        file_path=str(file_path),
    )
    db.add(paper)
    await db.commit()

    with (
        patch(
            "app.processing.service.extract_pdf_text",
            new=AsyncMock(return_value="some extracted text"),
        ),
        patch(
            "app.processing.service.generate_tags",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await process_paper(paper_id)

    schedule_mock.assert_called_once()
