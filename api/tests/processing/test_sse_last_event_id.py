"""SSE Last-Event-ID resume behavior for /api/papers/:id/status."""

import uuid

import pytest

from app.core.enums import SourceType, StepName
from app.papers.models import Paper
from app.processing.models import PaperStep, ProcessingEvent


@pytest.mark.asyncio
async def test_sse_emits_event_id_line(client, db):
    """SSE stream must emit `id: <N>` for each ProcessingEvent so clients can resume."""
    paper_id = uuid.uuid4()
    db.add(Paper(id=paper_id, source_type=SourceType.PDF))
    await db.flush()

    for step_name in StepName:
        status = "done" if step_name.value in ("uploading", "extracting", "summarizing") else "pending"
        db.add(PaperStep(paper_id=paper_id, step=step_name.value, status=status))

    ev = ProcessingEvent(paper_id=paper_id, step="extracting", detail="extract start")
    db.add(ev)
    await db.commit()
    await db.refresh(ev)

    response = await client.get(f"/api/papers/{paper_id}/status")

    assert response.status_code == 200
    body = response.text
    assert f"id: {ev.id}\n" in body
    assert "extracting" in body


@pytest.mark.asyncio
async def test_sse_last_event_id_header_filters_events(client, db):
    """Events with id <= Last-Event-ID must not be re-emitted."""
    paper_id = uuid.uuid4()
    db.add(Paper(id=paper_id, source_type=SourceType.PDF))
    await db.flush()

    for step_name in StepName:
        status = "done" if step_name.value in ("uploading", "extracting", "summarizing") else "pending"
        db.add(PaperStep(paper_id=paper_id, step=step_name.value, status=status))

    old_event = ProcessingEvent(paper_id=paper_id, step="uploading", detail="old")
    db.add(old_event)
    await db.flush()
    await db.refresh(old_event)

    new_event = ProcessingEvent(paper_id=paper_id, step="extracting", detail="new-after-resume")
    db.add(new_event)
    await db.commit()
    await db.refresh(new_event)

    response = await client.get(
        f"/api/papers/{paper_id}/status",
        headers={"Last-Event-ID": str(old_event.id)},
    )

    assert response.status_code == 200
    body = response.text
    assert "new-after-resume" in body
    assert f"id: {new_event.id}\n" in body
    # The old event (id <= Last-Event-ID) must NOT be re-streamed
    assert f"id: {old_event.id}\n" not in body


@pytest.mark.asyncio
async def test_sse_invalid_last_event_id_falls_back_to_zero(client, db):
    """A non-numeric Last-Event-ID must be treated as 0 (stream all events)."""
    paper_id = uuid.uuid4()
    db.add(Paper(id=paper_id, source_type=SourceType.PDF))
    await db.flush()

    for step_name in StepName:
        status = "done" if step_name.value in ("uploading", "extracting", "summarizing") else "pending"
        db.add(PaperStep(paper_id=paper_id, step=step_name.value, status=status))

    ev = ProcessingEvent(paper_id=paper_id, step="extracting", detail="after-garbage")
    db.add(ev)
    await db.commit()
    await db.refresh(ev)

    response = await client.get(
        f"/api/papers/{paper_id}/status",
        headers={"Last-Event-ID": "not-a-number"},
    )

    assert response.status_code == 200
    assert "after-garbage" in response.text
