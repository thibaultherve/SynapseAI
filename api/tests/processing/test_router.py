import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import SourceType, StepName, StepStatus
from app.papers.models import Paper
from app.processing.models import PaperStep, ProcessingEvent


# ---------------------------------------------------------------------------
# SSE stream tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_stream_events(client, db):
    """GET /api/papers/:id/status -> streams existing processing events."""
    paper_id = uuid.uuid4()
    paper = Paper(id=paper_id, source_type=SourceType.PDF)
    db.add(paper)
    await db.flush()

    # Set up steps with summarizing=done (readable -> terminal)
    for step_name in StepName:
        status = "done" if step_name.value in ("uploading", "extracting", "summarizing") else "pending"
        db.add(PaperStep(paper_id=paper_id, step=step_name.value, status=status))

    event = ProcessingEvent(
        paper_id=paper_id, step="extracting", detail="Extracting text from PDF..."
    )
    db.add(event)
    await db.commit()

    response = await client.get(f"/api/papers/{paper_id}/status")

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text
    assert "extracting" in body
    assert "complete" in body


# ---------------------------------------------------------------------------
# Retry endpoint tests (now step-based)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_step_in_error(client, db):
    """POST /api/papers/:id/retry/:step -> resets errored step and relaunches."""
    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        file_path="/tmp/test.pdf",
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        status = "error" if step_name.value == "extracting" else (
            "done" if step_name.value == "uploading" else "pending"
        )
        db.add(PaperStep(
            paper_id=paper_id,
            step=step_name.value,
            status=status,
            error_message="Previous failure" if step_name.value == "extracting" else None,
        ))
    await db.commit()

    with patch("app.processing.router.launch_processing"):
        response = await client.post(f"/api/papers/{paper_id}/retry/extracting")

    assert response.status_code == 200
    data = response.json()
    assert data["step"] == "extracting"
    assert data["status"] == "pending"
    assert data["error_message"] is None


@pytest.mark.asyncio
async def test_retry_step_not_in_error(client, db):
    """POST /api/papers/:id/retry/:step on non-error step -> 409."""
    paper_id = uuid.uuid4()
    paper = Paper(id=paper_id, source_type=SourceType.PDF)
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value, status="pending"))
    await db.commit()

    response = await client.post(f"/api/papers/{paper_id}/retry/uploading")

    assert response.status_code == 409
    assert "STEP_NOT_IN_ERROR" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_retry_invalid_step_name(client, db):
    """POST /api/papers/:id/retry/:step with invalid step -> 422."""
    paper_id = uuid.uuid4()
    paper = Paper(id=paper_id, source_type=SourceType.PDF)
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    response = await client.post(f"/api/papers/{paper_id}/retry/nonexistent")

    assert response.status_code == 422
    assert "VALIDATION_ERROR" in response.json()["error"]["code"]


# ---------------------------------------------------------------------------
# Pipeline integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processing_pipeline_pdf(db, tmp_upload_dir, mock_claude, mock_embedding):
    """Integration: process_paper runs full pipeline PDF -> extracted -> summarized."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    file_path = Path(tmp_upload_dir) / f"{paper_id}.pdf"
    file_path.write_bytes(b"%PDF-1.4 fake pdf content")

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        file_path=str(file_path),
    )
    db.add(paper)
    await db.flush()
    # Steps will be created by _ensure_steps in process_paper
    await db.commit()

    with (
        patch(
            "app.processing.service.extract_pdf_text",
            new_callable=AsyncMock,
            return_value="Extracted text from the test PDF document.",
        ),
        patch(
            "app.processing.service.generate_tags",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await process_paper(paper_id)

    # Verify paper reached readable state
    from app.core.database import async_session

    async with async_session() as verify_db:
        result = await verify_db.get(Paper, paper_id)
        assert result.short_summary is not None
        assert result.processed_at is not None
        # Check steps
        from sqlalchemy import select

        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s.status for s in steps}
        assert step_map["uploading"] == "done"
        assert step_map["extracting"] == "done"
        assert step_map["summarizing"] == "done"
        assert step_map["tagging"] == "done"


# --- Edge cases: SSE limits ---


@pytest.mark.asyncio
async def test_sse_too_many_per_paper(client, db):
    """GET /api/papers/:id/status when MAX_SSE_PER_PAPER already reached -> 429."""
    from app.processing.router import _sse_connections, MAX_SSE_PER_PAPER

    paper_id = uuid.uuid4()
    paper = Paper(id=paper_id, source_type=SourceType.PDF)
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
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
    paper = Paper(id=paper_id, source_type=SourceType.PDF)
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

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
    """POST /api/papers/:id/retry/:step with unknown id -> 404."""
    fake_id = uuid.uuid4()
    response = await client.post(f"/api/papers/{fake_id}/retry/extracting")

    assert response.status_code == 404
    assert "PAPER_NOT_FOUND" in response.json()["error"]["code"]
