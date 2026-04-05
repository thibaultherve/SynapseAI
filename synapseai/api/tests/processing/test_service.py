import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.database import async_session
from app.core.enums import PaperStatus, SourceType
from app.papers.models import Paper


@pytest.mark.asyncio
async def test_process_paper_not_found(db):
    """process_paper with nonexistent paper_id -> logs error and returns without crash."""
    from app.processing.service import process_paper

    fake_id = uuid.uuid4()
    await process_paper(fake_id)

    # No paper should exist, nothing to assert except no exception raised


@pytest.mark.asyncio
async def test_process_paper_no_text_extracted(db, tmp_upload_dir, mock_claude):
    """process_paper when extraction yields no text -> paper goes to error state."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        status=PaperStatus.UPLOADING.value,
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
        result = await verify_db.get(Paper, paper_id)
        assert result.status == PaperStatus.ERROR.value
        assert "No text content" in result.error_message


@pytest.mark.asyncio
async def test_process_paper_web_pipeline(db, mock_claude):
    """process_paper with web source -> downloads, extracts, summarizes."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB.value,
        status=PaperStatus.UPLOADING.value,
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
        assert result.status == PaperStatus.SUMMARIZED.value
        assert result.extracted_text == "Extracted web text from the research paper."
        assert result.short_summary is not None
        assert result.processed_at is not None


@pytest.mark.asyncio
async def test_process_paper_extraction_error_sets_error_state(db, tmp_upload_dir):
    """process_paper when extraction raises -> paper goes to error state with message."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        status=PaperStatus.UPLOADING.value,
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
        result = await verify_db.get(Paper, paper_id)
        assert result.status == PaperStatus.ERROR.value
        assert "PDF corrupted" in result.error_message
