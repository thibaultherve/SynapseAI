"""Tests T31-T37: insights router (endpoints, filters, rating, refresh, delete)."""


import pytest

from app.insights.debouncer import insight_debouncer
from app.insights.models import Insight, InsightPaper


async def _make_insight(
    db,
    *,
    type: str = "trend",
    title: str = "Sample insight",
    confidence: str = "medium",
    rating: int | None = None,
    paper_ids: list | None = None,
) -> Insight:
    from app.insights.service import _normalize_title

    insight = Insight(
        type=type,
        title=title,
        content="content",
        evidence="evidence",
        confidence=confidence,
        rating=rating,
        title_normalized=_normalize_title(title),
    )
    db.add(insight)
    await db.flush()
    for pid in paper_ids or []:
        db.add(InsightPaper(insight_id=insight.id, paper_id=pid))
    await db.commit()
    await db.refresh(insight)
    return insight


# ---------------------------------------------------------------------------
# T31 — GET /api/insights with filters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_insights_filters_type_confidence_rating(
    client, db, paper_factory
):
    p = await paper_factory(title="P")
    await _make_insight(
        db, type="trend", confidence="high", rating=1, paper_ids=[p.id]
    )
    await _make_insight(
        db, type="gap", confidence="low", rating=-1, paper_ids=[p.id]
    )

    # type filter
    resp = await client.get("/api/insights?type=trend")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["type"] == "trend"

    # confidence filter
    resp = await client.get("/api/insights?confidence=low")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["confidence"] == "low"

    # rating filter
    resp = await client.get("/api/insights?rating=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["rating"] == 1


# ---------------------------------------------------------------------------
# T32 — list excludes orphans
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_insights_excludes_without_supporting_papers(
    client, db, paper_factory
):
    p = await paper_factory(title="P")
    await _make_insight(
        db, type="trend", title="linked", paper_ids=[p.id]
    )
    await _make_insight(db, type="gap", title="orphan", paper_ids=[])

    resp = await client.get("/api/insights")
    assert resp.status_code == 200
    titles = [i["title"] for i in resp.json()]
    assert "linked" in titles
    assert "orphan" not in titles


# ---------------------------------------------------------------------------
# T33 — GET /api/insights/:id hydrates supporting_papers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_insight_hydrates_supporting_papers(
    client, db, paper_factory
):
    p1 = await paper_factory(title="Paper One")
    p2 = await paper_factory(title="Paper Two")
    insight = await _make_insight(
        db, type="trend", paper_ids=[p1.id, p2.id]
    )

    resp = await client.get(f"/api/insights/{insight.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["supporting_papers"]) == 2
    titles = {p["title"] for p in body["supporting_papers"]}
    assert titles == {"Paper One", "Paper Two"}


@pytest.mark.asyncio
async def test_get_insight_unknown_returns_404(client):
    resp = await client.get("/api/insights/99999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "INSIGHT_NOT_FOUND"


# ---------------------------------------------------------------------------
# T34 — PATCH /api/insights/:id/rating
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_rating_accepts_valid_values(client, db, paper_factory):
    p = await paper_factory(title="P")
    insight = await _make_insight(db, paper_ids=[p.id])

    for rating in (1, -1, None):
        resp = await client.patch(
            f"/api/insights/{insight.id}/rating",
            json={"rating": rating},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["rating"] == rating


@pytest.mark.asyncio
async def test_patch_rating_rejects_invalid(client, db, paper_factory):
    p = await paper_factory(title="P")
    insight = await _make_insight(db, paper_ids=[p.id])

    resp = await client.patch(
        f"/api/insights/{insight.id}/rating",
        json={"rating": 2},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# T35 — POST /refresh : 409 when lock held
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_returns_409_when_locked(client):
    await insight_debouncer.lock.acquire()
    try:
        resp = await client.post("/api/insights/refresh")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "INSIGHT_REFRESH_BUSY"
    finally:
        insight_debouncer.lock.release()


# ---------------------------------------------------------------------------
# T36 — POST /refresh : hash match -> skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_returns_skipped_on_hash_match(
    client, db, paper_factory, crossref_factory, monkeypatch
):
    from app.insights.claude_prompts import InsightOutput

    # Reset debouncer state for the test
    insight_debouncer.reset()

    p1 = await paper_factory(title="A")
    p2 = await paper_factory(title="B")
    await crossref_factory(p1.id, p2.id)

    async def fake_claude(**kwargs):
        return [
            InsightOutput(
                type="trend",
                title="T",
                content="C",
                evidence="E",
                confidence="medium",
                supporting_papers=[str(p1.id), str(p2.id)],
            )
        ]

    monkeypatch.setattr(
        "app.insights.service.generate_insights_from_claude", fake_claude
    )

    from app.ratelimit import limiter

    first = await client.post("/api/insights/refresh")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "generated"

    # Reset rate-limit state (1/10min blocks the second call in-test).
    limiter.reset()

    second = await client.post("/api/insights/refresh")
    assert second.status_code == 200
    assert second.json()["status"] == "skipped"
    assert second.json()["skipped"] is True


# ---------------------------------------------------------------------------
# T37 — DELETE /api/insights/:id : 204 + cascade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_insight_cascades_insight_paper(
    client, db, paper_factory
):
    p = await paper_factory(title="P")
    insight = await _make_insight(db, paper_ids=[p.id])

    resp = await client.delete(f"/api/insights/{insight.id}")
    assert resp.status_code == 204

    # Ensure the insight and its insight_paper rows are gone.
    from sqlalchemy import select

    remaining = (
        await db.execute(select(Insight).where(Insight.id == insight.id))
    ).scalar_one_or_none()
    assert remaining is None

    links = (
        await db.execute(
            select(InsightPaper).where(InsightPaper.insight_id == insight.id)
        )
    ).scalars().all()
    assert links == []


@pytest.mark.asyncio
async def test_delete_insight_unknown_returns_404(client):
    resp = await client.delete("/api/insights/99999")
    assert resp.status_code == 404
