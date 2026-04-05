import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import PaperStatus, SourceType
from app.papers.models import Paper
from app.processing.models import ProcessingEvent


@pytest.mark.asyncio
async def test_sse_stream_events(client, db):
    """GET /api/papers/:id/status -> streams existing processing events."""
    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        status=PaperStatus.SUMMARIZED,
    )
    db.add(paper)
    await db.flush()

    event = ProcessingEvent(
        paper_id=paper_id,
        step="extracting",
        detail="Extracting text from PDF...",
    )
    db.add(event)
    await db.commit()

    response = await client.get(f"/api/papers/{paper_id}/status")

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text
    assert "extracting" in body
    assert "complete" in body


@pytest.mark.asyncio
async def test_retry_paper_in_error(client, db):
    """POST /api/papers/:id/retry -> relaunches processing for errored paper."""
    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        status=PaperStatus.ERROR,
        error_message="Previous failure",
    )
    db.add(paper)
    await db.commit()

    with patch("app.processing.router.launch_processing"):
        response = await client.post(f"/api/papers/{paper_id}/retry")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "uploading"
    assert data["error_message"] is None


@pytest.mark.asyncio
async def test_retry_paper_not_in_error(client, db):
    """POST /api/papers/:id/retry on non-error paper -> 409."""
    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        status=PaperStatus.UPLOADING,
    )
    db.add(paper)
    await db.commit()

    response = await client.post(f"/api/papers/{paper_id}/retry")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_processing_pipeline_pdf(db, tmp_upload_dir, mock_claude):
    """Integration: process_paper runs full pipeline PDF -> extracted -> summarized."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    file_path = Path(tmp_upload_dir) / f"{paper_id}.pdf"
    file_path.write_bytes(b"%PDF-1.4 fake pdf content")

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        status=PaperStatus.UPLOADING.value,
        file_path=str(file_path),
    )
    db.add(paper)
    await db.commit()

    with patch(
        "app.processing.service.extract_pdf_text",
        new_callable=AsyncMock,
        return_value="Extracted text from the test PDF document.",
    ):
        await process_paper(paper_id)

    # Verify paper reached summarized state
    from app.core.database import async_session

    async with async_session() as verify_db:
        result = await verify_db.get(Paper, paper_id)
        assert result.status == PaperStatus.SUMMARIZED.value
        assert result.short_summary is not None
        assert result.processed_at is not None


# --- Edge cases: SSE limits ---


@pytest.mark.asyncio
async def test_sse_too_many_per_paper(client, db):
    """GET /api/papers/:id/status when MAX_SSE_PER_PAPER already reached -> 429."""
    from app.processing.router import _sse_connections, MAX_SSE_PER_PAPER

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        status=PaperStatus.EXTRACTING,
    )
    db.add(paper)
    await db.commit()

    key = str(paper_id)
    _sse_connections[key] = MAX_SSE_PER_PAPER
    try:
        response = await client.get(f"/api/papers/{paper_id}/status")
        assert response.status_code == 429
    finally:
        _sse_connections.pop(key, None)


@pytest.mark.asyncio
async def test_sse_server_at_capacity(client, db):
    """GET /api/papers/:id/status when MAX_SSE_TOTAL already reached -> 503."""
    from app.processing.router import _sse_connections, MAX_SSE_TOTAL

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        status=PaperStatus.EXTRACTING,
    )
    db.add(paper)
    await db.commit()

    # Fill up total SSE connections with a fake key
    _sse_connections["__fake__"] = MAX_SSE_TOTAL
    try:
        response = await client.get(f"/api/papers/{paper_id}/status")
        assert response.status_code == 503
    finally:
        _sse_connections.pop("__fake__", None)


@pytest.mark.asyncio
async def test_sse_paper_not_found(client):
    """GET /api/papers/:id/status with unknown id -> 404."""
    fake_id = uuid.uuid4()
    response = await client.get(f"/api/papers/{fake_id}/status")

    assert response.status_code == 404
    assert "PAPER_NOT_FOUND" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_retry_paper_not_found(client):
    """POST /api/papers/:id/retry with unknown id -> 404."""
    fake_id = uuid.uuid4()
    response = await client.post(f"/api/papers/{fake_id}/retry")

    assert response.status_code == 404
    assert "PAPER_NOT_FOUND" in response.json()["error"]["code"]
