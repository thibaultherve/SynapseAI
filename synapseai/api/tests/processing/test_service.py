import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.database import async_session
from app.core.enums import SourceType
from app.papers.models import Paper
from app.processing.models import PaperStep


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
async def test_process_paper_web_pipeline(db, mock_claude):
    """process_paper with web source -> downloads, extracts, summarizes."""
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
