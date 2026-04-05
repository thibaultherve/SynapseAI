"""T7: Computed status (readable, enriched, error, processing)
   T8: PaperResponse includes steps + computed status
"""

import uuid

import pytest

from app.papers.schemas import PaperStepResponse
from app.papers.utils import compute_paper_status


# ---------------------------------------------------------------------------
# T7: compute_paper_status
# ---------------------------------------------------------------------------


def _make_steps(overrides: dict[str, str] | None = None) -> list[PaperStepResponse]:
    """Build a list of PaperStepResponse with given step->status overrides."""
    defaults = {
        "uploading": "pending",
        "extracting": "pending",
        "summarizing": "pending",
        "tagging": "pending",
        "embedding": "pending",
        "crossrefing": "pending",
    }
    defaults.update(overrides or {})
    return [
        PaperStepResponse(step=name, status=status)
        for name, status in defaults.items()
    ]


def test_computed_status_pending():
    """All steps pending -> status = pending."""
    steps = _make_steps()
    assert compute_paper_status(steps) == "pending"


def test_computed_status_processing():
    """At least one step processing -> status = processing."""
    steps = _make_steps({"uploading": "processing"})
    assert compute_paper_status(steps) == "processing"


def test_computed_status_error():
    """Any step in error -> status = error (highest priority)."""
    steps = _make_steps({
        "uploading": "done",
        "extracting": "error",
        "summarizing": "done",  # Even with other steps done
    })
    assert compute_paper_status(steps) == "error"


def test_computed_status_readable():
    """Summarizing done (but not all steps done) -> status = readable."""
    steps = _make_steps({
        "uploading": "done",
        "extracting": "done",
        "summarizing": "done",
        "tagging": "pending",
        "embedding": "pending",
    })
    assert compute_paper_status(steps) == "readable"


def test_computed_status_enriched():
    """All steps done (except crossrefing can be pending) -> status = enriched."""
    steps = _make_steps({
        "uploading": "done",
        "extracting": "done",
        "summarizing": "done",
        "tagging": "done",
        "embedding": "done",
        "crossrefing": "pending",
    })
    assert compute_paper_status(steps) == "enriched"


def test_computed_status_enriched_all_done():
    """All steps including crossrefing done -> status = enriched."""
    steps = _make_steps({
        "uploading": "done",
        "extracting": "done",
        "summarizing": "done",
        "tagging": "done",
        "embedding": "done",
        "crossrefing": "done",
    })
    assert compute_paper_status(steps) == "enriched"


def test_computed_status_error_overrides_processing():
    """Error takes priority over processing."""
    steps = _make_steps({
        "uploading": "done",
        "extracting": "processing",
        "summarizing": "error",
    })
    assert compute_paper_status(steps) == "error"


def test_computed_status_empty_steps():
    """No steps at all -> status = pending."""
    assert compute_paper_status([]) == "pending"


# ---------------------------------------------------------------------------
# T8: PaperResponse includes steps + computed status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_response_includes_steps_and_status(client, paper_factory):
    """T8: GET /api/papers/:id returns steps list and computed status."""
    paper = await paper_factory(
        steps={
            "uploading": "done",
            "extracting": "done",
            "summarizing": "done",
        },
    )

    response = await client.get(f"/api/papers/{paper.id}")

    assert response.status_code == 200
    data = response.json()
    assert "steps" in data
    assert len(data["steps"]) == 6
    assert data["status"] == "readable"


@pytest.mark.asyncio
async def test_paper_response_error_status(client, paper_factory):
    """PaperResponse reflects error status when a step is in error."""
    paper = await paper_factory(
        steps={
            "uploading": "done",
            "extracting": "error",
        },
    )

    response = await client.get(f"/api/papers/{paper.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_paper_list_includes_steps(client, paper_factory):
    """PaperSummaryResponse (list view) also includes steps and computed status."""
    await paper_factory(
        steps={
            "uploading": "done",
            "extracting": "done",
            "summarizing": "done",
        },
    )

    response = await client.get("/api/papers")

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["status"] == "readable"
    assert len(data[0]["steps"]) == 6
