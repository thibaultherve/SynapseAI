"""Tests T21-T28: insights service (normalize, dedup, generate, cleanup)."""

import pytest
from sqlalchemy import select

from app.insights import service
from app.insights.claude_prompts import InsightOutput
from app.insights.models import Insight, InsightPaper
from app.insights.schemas import InsightFilters

# ---------------------------------------------------------------------------
# T21 — _normalize_title
# ---------------------------------------------------------------------------

def test_normalize_title_lowercases_and_strips_stopwords():
    assert service._normalize_title("The Role Of AI In Healthcare") == "role ai healthcare"


def test_normalize_title_collapses_whitespace_and_punctuation():
    assert service._normalize_title("   AI:   neuro-science!  ") == "ai neuro science"


def test_normalize_title_handles_empty_input():
    assert service._normalize_title(None) == ""
    assert service._normalize_title("") == ""


def test_normalize_title_handles_french_stopwords():
    # "le", "la", "de" are stopwords
    assert service._normalize_title("Le role de l'IA dans la sante") == "role l ia sante"


# ---------------------------------------------------------------------------
# T22 — _dedup_and_persist : ratio > threshold => UPDATE existing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedup_and_persist_merges_on_similar_title(db, paper_factory):
    p1 = await paper_factory(title="A")
    p2 = await paper_factory(title="B")

    existing = Insight(
        type="trend",
        title="The role of AI in healthcare",
        title_normalized=service._normalize_title("The role of AI in healthcare"),
        content="Original content",
        evidence="Original evidence",
        confidence="medium",
    )
    db.add(existing)
    await db.commit()
    await db.refresh(existing)

    new = InsightOutput(
        type="trend",
        title="The role of AI in healthcare systems",
        content="New content",
        evidence="New evidence",
        confidence="high",
        supporting_papers=[str(p1.id), str(p2.id)],
    )

    action, persisted = await service._dedup_and_persist(db, new, [existing])
    await db.commit()

    assert action == "merged"
    assert persisted.id == existing.id
    assert "New evidence" in (persisted.evidence or "")
    assert "Original evidence" in (persisted.evidence or "")

    links = (
        await db.execute(
            select(InsightPaper).where(InsightPaper.insight_id == existing.id)
        )
    ).scalars().all()
    assert len(links) == 2


# ---------------------------------------------------------------------------
# T23 — _dedup_and_persist : ratio < threshold => INSERT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedup_and_persist_inserts_when_no_match(db, paper_factory):
    p1 = await paper_factory(title="A")
    p2 = await paper_factory(title="B")

    new = InsightOutput(
        type="gap",
        title="Missing research on quantum biology",
        content="No papers address this",
        evidence="Evidence here",
        confidence="low",
        supporting_papers=[str(p1.id), str(p2.id), str(p1.id)],  # duplicates ignored
    )

    action, persisted = await service._dedup_and_persist(db, new, [])
    await db.commit()

    assert action == "inserted"
    assert persisted.id is not None
    assert persisted.title_normalized == service._normalize_title(new.title)

    links = (
        await db.execute(
            select(InsightPaper).where(InsightPaper.insight_id == persisted.id)
        )
    ).scalars().all()
    # 2 unique papers (3rd is duplicate of 1st -> ON CONFLICT DO NOTHING)
    assert len(links) == 2


# ---------------------------------------------------------------------------
# T26 — generate_insights : hash idempotence skip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_insights_skips_when_hash_matches(
    db, paper_factory, crossref_factory, monkeypatch
):
    p1 = await paper_factory(title="A", short_summary="About X")
    p2 = await paper_factory(title="B", short_summary="About Y")
    await crossref_factory(p1.id, p2.id)

    called = {"count": 0}

    async def fake_claude(**kwargs):
        called["count"] += 1
        return []

    monkeypatch.setattr(
        "app.insights.service.generate_insights_from_claude", fake_claude
    )

    first = await service.generate_insights(db, last_hash=None)
    assert first["status"] == "generated"
    assert called["count"] == 1

    second = await service.generate_insights(db, last_hash=first["hash"])
    assert second["status"] == "skipped"
    assert second["skipped"] is True
    # Claude NOT called a second time
    assert called["count"] == 1


# ---------------------------------------------------------------------------
# T27 — generate_insights : Claude mock -> insights persisted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_insights_persists_outputs(
    db, paper_factory, crossref_factory, monkeypatch
):
    p1 = await paper_factory(title="A", short_summary="Alpha")
    p2 = await paper_factory(title="B", short_summary="Beta")
    p3 = await paper_factory(title="C", short_summary="Gamma")
    await crossref_factory(p1.id, p2.id)
    await crossref_factory(p2.id, p3.id)

    async def fake_claude(**kwargs):
        return [
            InsightOutput(
                type="trend",
                title="Convergent pattern on X",
                content="Several papers converge on X.",
                evidence="P1 and P2 agree.",
                confidence="high",
                supporting_papers=[str(p1.id), str(p2.id)],
            ),
            InsightOutput(
                type="gap",
                title="Missing evaluation of Y",
                content="No paper evaluates Y.",
                evidence="Gap across P1/P2/P3.",
                confidence="medium",
                supporting_papers=[str(p1.id), str(p2.id), str(p3.id)],
            ),
        ]

    monkeypatch.setattr(
        "app.insights.service.generate_insights_from_claude", fake_claude
    )

    result = await service.generate_insights(db, last_hash=None)
    assert result["status"] == "generated"
    assert result["insights_new"] == 2
    assert result["insights_merged"] == 0

    persisted = (await db.execute(select(Insight))).scalars().all()
    assert len(persisted) == 2


# ---------------------------------------------------------------------------
# T28 — cleanup_orphan_insights
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_orphan_insights_deletes_those_without_links(
    db, paper_factory
):
    p = await paper_factory(title="A")

    linked = Insight(
        type="trend", title="linked", content="x",
        confidence="low", title_normalized="linked",
    )
    orphan = Insight(
        type="gap", title="orphan", content="x",
        confidence="low", title_normalized="orphan",
    )
    db.add_all([linked, orphan])
    await db.flush()
    db.add(InsightPaper(insight_id=linked.id, paper_id=p.id))
    await db.commit()

    deleted = await service.cleanup_orphan_insights(db)
    assert deleted == 1

    remaining = (await db.execute(select(Insight))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == linked.id


# ---------------------------------------------------------------------------
# Bonus: list_insights excludes orphans (foundation for T32)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_insights_excludes_orphans(db, paper_factory):
    p = await paper_factory(title="A")

    linked = Insight(
        type="trend", title="linked", content="x",
        confidence="low", title_normalized="linked",
    )
    orphan = Insight(
        type="gap", title="orphan", content="x",
        confidence="low", title_normalized="orphan",
    )
    db.add_all([linked, orphan])
    await db.flush()
    db.add(InsightPaper(insight_id=linked.id, paper_id=p.id))
    await db.commit()

    results = await service.list_insights(db, InsightFilters())
    assert len(results) == 1
    assert results[0].id == linked.id
