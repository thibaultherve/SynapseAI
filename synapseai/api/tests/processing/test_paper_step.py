"""Tests T1-T6: paper_step creation, transitions, and endpoints."""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.database import async_session
from app.core.enums import SourceType, StepName, StepStatus
from app.papers.models import Paper
from app.processing.models import PaperStep


# ---------------------------------------------------------------------------
# T1: Creation of 6 paper_step rows when paper created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_paper_creates_six_steps(client, tmp_upload_dir):
    """T1: Creating a paper via API generates 6 paper_step rows (all pending)."""
    pdf_content = b"%PDF-1.4 fake pdf content"

    with patch("app.processing.task_registry.launch_processing"):
        response = await client.post(
            "/api/papers/upload",
            files={"file": ("test.pdf", pdf_content, "application/pdf")},
        )

    assert response.status_code == 201
    data = response.json()
    assert len(data["steps"]) == 6

    step_names = {s["step"] for s in data["steps"]}
    assert step_names == {
        "uploading", "extracting", "summarizing",
        "tagging", "embedding", "crossrefing",
    }
    assert all(s["status"] == "pending" for s in data["steps"])


# ---------------------------------------------------------------------------
# T2: Step transitions (pending -> processing -> done, pending -> processing -> error)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_transitions_pdf_success(db, tmp_upload_dir, mock_claude):
    """T2a: Full pipeline transitions steps through pending -> processing -> done."""
    from app.processing.service import process_paper

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

    with patch(
        "app.processing.service.extract_pdf_text",
        new_callable=AsyncMock,
        return_value="Extracted text from test PDF.",
    ):
        await process_paper(paper_id)

    async with async_session() as verify_db:
        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s for s in steps}

        # First 3 steps should be done with timestamps
        for name in ("uploading", "extracting", "summarizing"):
            assert step_map[name].status == StepStatus.DONE
            assert step_map[name].completed_at is not None

        # Remaining steps stay pending
        for name in ("tagging", "embedding", "crossrefing"):
            assert step_map[name].status == StepStatus.PENDING


@pytest.mark.asyncio
async def test_step_transitions_error(db, tmp_upload_dir):
    """T2b: Extraction failure transitions step to error with message."""
    from app.processing.service import process_paper

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF.value,
        file_path="missing.pdf",
    )
    db.add(paper)
    await db.commit()

    with patch(
        "app.processing.service.extract_pdf_text",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Corrupt PDF"),
    ):
        await process_paper(paper_id)

    async with async_session() as verify_db:
        steps = (await verify_db.execute(
            select(PaperStep).where(PaperStep.paper_id == paper_id)
        )).scalars().all()
        step_map = {s.step: s for s in steps}

        assert step_map["uploading"].status == StepStatus.DONE
        assert step_map["extracting"].status == StepStatus.ERROR
        assert "Corrupt PDF" in step_map["extracting"].error_message
        # Subsequent steps remain pending
        assert step_map["summarizing"].status == StepStatus.PENDING


# ---------------------------------------------------------------------------
# T3: GET /api/papers/:id/steps returns 6 steps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_paper_steps_returns_six(client, paper_factory):
    """T3: GET /api/papers/:id/steps returns all 6 steps."""
    paper = await paper_factory()

    response = await client.get(f"/api/papers/{paper.id}/steps")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 6
    step_names = {s["step"] for s in data}
    assert step_names == {s.value for s in StepName}


@pytest.mark.asyncio
async def test_get_paper_steps_not_found(client):
    """GET /api/papers/:id/steps with unknown id -> 404."""
    fake_id = uuid.uuid4()
    response = await client.get(f"/api/papers/{fake_id}/steps")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# T4: POST retry/:step — step in error -> relaunches OK
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_step_in_error_relaunches(client, db):
    """T4: POST retry/:step with errored step -> resets to pending, relaunches."""
    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB,
        url="https://example.com/paper",
        extracted_text="Some text",
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        status = "error" if step_name.value == "summarizing" else (
            "done" if step_name.value in ("uploading", "extracting") else "pending"
        )
        db.add(PaperStep(
            paper_id=paper_id,
            step=step_name.value,
            status=status,
            error_message="Claude timeout" if step_name.value == "summarizing" else None,
        ))
    await db.commit()

    with patch("app.processing.router.launch_processing") as mock_launch:
        response = await client.post(f"/api/papers/{paper_id}/retry/summarizing")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["error_message"] is None
    mock_launch.assert_called_once()


# ---------------------------------------------------------------------------
# T5: POST retry/:step — step not in error -> 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_step_not_in_error_409(client, paper_factory):
    """T5: POST retry/:step on a step that is 'done' -> 409."""
    paper = await paper_factory(
        steps={"uploading": "done", "extracting": "pending"},
    )

    response = await client.post(f"/api/papers/{paper.id}/retry/uploading")

    assert response.status_code == 409
    assert "STEP_NOT_IN_ERROR" in response.json()["error"]["code"]


# ---------------------------------------------------------------------------
# T6: POST retry/:step — can_retry precondition fail -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_step_precondition_fail(client, db):
    """T6: POST retry/summarizing when extracted_text is null -> 422."""
    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        # No extracted_text — precondition for summarizing fails
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        status = "error" if step_name.value == "summarizing" else "pending"
        db.add(PaperStep(
            paper_id=paper_id,
            step=step_name.value,
            status=status,
            error_message="fail" if step_name.value == "summarizing" else None,
        ))
    await db.commit()

    response = await client.post(f"/api/papers/{paper_id}/retry/summarizing")

    assert response.status_code == 422
    assert "RETRY_PRECONDITION_FAILED" in response.json()["error"]["code"]
