import hashlib
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Literal

from sqlalchemy import delete, desc, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import insight_settings
from app.insights.claude_prompts import (
    InsightOutput,
    generate_insights_from_claude,
)
from app.insights.models import Insight, InsightPaper
from app.insights.schemas import (
    InsightFilters,
    InsightResponse,
)
from app.papers.models import Paper
from app.papers.schemas import PaperSummaryResponse
from app.processing.claude_service import sanitize_summary_for_reuse
from app.processing.models import CrossReference

logger = logging.getLogger(__name__)


# Minimal stopword list — enough to normalize titles for dedup comparison.
_STOPWORDS_EN = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "by", "as", "from",
})
_STOPWORDS_FR = frozenset({
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou",
    "mais", "a", "au", "aux", "en", "pour", "avec", "sur", "dans",
    "par", "est", "sont", "ce", "ces", "cet", "cette",
})
_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_FR


_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_title(text: str | None) -> str:
    """Lowercase + strip punctuation + drop basic EN/FR stopwords + collapse whitespace."""
    if not text:
        return ""
    lowered = text.lower()
    stripped = _NON_WORD_RE.sub(" ", lowered)
    tokens = [tok for tok in stripped.split() if tok and tok not in _STOPWORDS]
    return _WHITESPACE_RE.sub(" ", " ".join(tokens)).strip()


def compute_context_hash(
    paper_ids: list[uuid.UUID],
    max_detected_at: datetime | None,
) -> str:
    """Deterministic hash of the generation context, used for idempotence."""
    sorted_ids = sorted(str(pid) for pid in paper_ids)
    basis = "|".join(sorted_ids) + "@" + (
        max_detected_at.isoformat() if max_detected_at else ""
    )
    return hashlib.sha256(basis.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Hydration
# ---------------------------------------------------------------------------

async def _hydrate_supporting_papers(
    db: AsyncSession, insight_ids: list[int]
) -> dict[int, list[Paper]]:
    """Return {insight_id: [Paper, ...]} loading papers via insight_paper junction."""
    if not insight_ids:
        return {}
    rows = (
        await db.execute(
            select(InsightPaper.insight_id, Paper)
            .join(Paper, Paper.id == InsightPaper.paper_id)
            .where(InsightPaper.insight_id.in_(insight_ids))
        )
    ).all()

    result: dict[int, list[Paper]] = {iid: [] for iid in insight_ids}
    for insight_id, paper in rows:
        result[insight_id].append(paper)
    return result


def _to_response(insight: Insight, papers: list[Paper]) -> InsightResponse:
    return InsightResponse(
        id=insight.id,
        type=insight.type,
        title=insight.title,
        content=insight.content,
        evidence=insight.evidence,
        confidence=insight.confidence,
        rating=insight.rating,
        supporting_papers=[PaperSummaryResponse.model_validate(p) for p in papers],
        detected_at=insight.detected_at,
        updated_at=insight.updated_at,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def list_insights(
    db: AsyncSession, filters: InsightFilters
) -> list[InsightResponse]:
    """List insights. Excludes those without any insight_paper row."""
    supporting_exists = exists().where(InsightPaper.insight_id == Insight.id)
    query = select(Insight).where(supporting_exists)

    if filters.type:
        query = query.where(Insight.type == filters.type.value)
    if filters.confidence:
        query = query.where(Insight.confidence == filters.confidence.value)
    if filters.rating is not None:
        query = query.where(Insight.rating == filters.rating)

    query = (
        query.order_by(desc(Insight.updated_at))
        .limit(filters.limit)
        .offset(filters.offset)
    )
    insights = list((await db.execute(query)).scalars().all())

    by_id = await _hydrate_supporting_papers(db, [i.id for i in insights])
    return [_to_response(i, by_id.get(i.id, [])) for i in insights]


async def get_insight(db: AsyncSession, insight: Insight) -> InsightResponse:
    by_id = await _hydrate_supporting_papers(db, [insight.id])
    return _to_response(insight, by_id.get(insight.id, []))


async def update_rating(
    db: AsyncSession, insight: Insight, rating: Literal[1, -1] | None
) -> InsightResponse:
    insight.rating = rating
    await db.commit()
    await db.refresh(insight)
    return await get_insight(db, insight)


async def delete_insight(db: AsyncSession, insight: Insight) -> None:
    await db.delete(insight)
    await db.commit()


async def cleanup_orphan_insights(db: AsyncSession) -> int:
    """Delete insights with no insight_paper rows. Returns count deleted."""
    supporting_exists = exists().where(InsightPaper.insight_id == Insight.id)
    result = await db.execute(
        delete(Insight).where(~supporting_exists).returning(Insight.id)
    )
    deleted = result.scalars().all()
    await db.commit()
    return len(deleted)


# ---------------------------------------------------------------------------
# Dedup + persist
# ---------------------------------------------------------------------------

async def _dedup_and_persist(
    db: AsyncSession,
    new: InsightOutput,
    existing_by_type: list[Insight],
) -> tuple[Literal["inserted", "merged"], Insight]:
    """Dedup against same-type insights via SequenceMatcher.

    UPDATE if best ratio > threshold, else INSERT.
    """
    new_title_normalized = _normalize_title(new.title)
    threshold = insight_settings.INSIGHT_DEDUP_THRESHOLD

    best_match: Insight | None = None
    best_ratio = 0.0
    for existing in existing_by_type:
        existing_norm = existing.title_normalized or _normalize_title(existing.title)
        ratio = SequenceMatcher(None, existing_norm, new_title_normalized).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = existing

    new_paper_ids: list[uuid.UUID] = []
    for pid in new.supporting_papers:
        try:
            new_paper_ids.append(uuid.UUID(pid))
        except (ValueError, AttributeError):
            continue

    if best_match is not None and best_ratio > threshold:
        # Merge: extend evidence + add supporting_papers rows
        if new.evidence:
            existing_evidence = best_match.evidence or ""
            combined = (existing_evidence + "\n\n" + new.evidence).strip()
            best_match.evidence = combined[
                : insight_settings.INSIGHT_MAX_EVIDENCE_LENGTH
            ]
        if new_paper_ids:
            stmt = (
                pg_insert(InsightPaper)
                .values(
                    [
                        {"insight_id": best_match.id, "paper_id": pid}
                        for pid in new_paper_ids
                    ]
                )
                .on_conflict_do_nothing(index_elements=["insight_id", "paper_id"])
            )
            await db.execute(stmt)
        await db.flush()
        return "merged", best_match

    # Insert fresh
    insight = Insight(
        type=new.type,
        title=new.title,
        content=new.content,
        evidence=new.evidence,
        confidence=new.confidence,
        title_normalized=new_title_normalized,
    )
    db.add(insight)
    await db.flush()

    if new_paper_ids:
        stmt = (
            pg_insert(InsightPaper)
            .values(
                [
                    {"insight_id": insight.id, "paper_id": pid}
                    for pid in new_paper_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=["insight_id", "paper_id"])
        )
        await db.execute(stmt)

    await db.flush()
    return "inserted", insight


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

async def _build_generation_context(
    db: AsyncSession,
) -> tuple[str, str, str, set[str], str]:
    """Assemble the generation context.

    Returns (existing_insights_json, papers_json, crossrefs_json,
    valid_paper_ids, context_hash).
    """
    # 1. Existing insights (top rated + recent fallback)
    top_rated_q = (
        select(Insight)
        .where(Insight.rating == 1)
        .order_by(desc(Insight.updated_at))
        .limit(insight_settings.INSIGHT_CONTEXT_TOP_RATED)
    )
    top_rated = list((await db.execute(top_rated_q)).scalars().all())

    remaining = insight_settings.INSIGHT_CONTEXT_TOP_RATED - len(top_rated)
    recent: list[Insight] = []
    if remaining > 0:
        seen_ids = {i.id for i in top_rated}
        recent_q = (
            select(Insight)
            .order_by(desc(Insight.updated_at))
            .limit(remaining + len(top_rated))
        )
        for insight in (await db.execute(recent_q)).scalars().all():
            if insight.id not in seen_ids:
                recent.append(insight)
                if len(recent) >= remaining:
                    break

    existing_payload = [
        {
            "id": i.id,
            "type": i.type,
            "title": i.title,
            "confidence": i.confidence,
        }
        for i in (top_rated + recent)
    ]
    existing_insights_json = json.dumps(existing_payload)

    # 2. Recent cross-references (lookback window)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        hours=insight_settings.INSIGHT_LOOKBACK_HOURS
    )
    crossref_q = (
        select(CrossReference)
        .where(CrossReference.detected_at >= cutoff)
        .order_by(desc(CrossReference.detected_at))
        .limit(insight_settings.INSIGHT_MAX_CROSSREFS)
    )
    crossrefs = list((await db.execute(crossref_q)).scalars().all())

    # 3. Papers involved in these cross-references
    paper_ids: set[uuid.UUID] = set()
    for cr in crossrefs:
        paper_ids.add(cr.paper_a)
        paper_ids.add(cr.paper_b)

    papers: list[Paper] = []
    if paper_ids:
        papers = list(
            (
                await db.execute(
                    select(Paper).where(Paper.id.in_(paper_ids))
                )
            )
            .scalars()
            .unique()
            .all()
        )

    summary_chars = insight_settings.INSIGHT_CONTEXT_SUMMARY_CHARS
    papers_payload = [
        {
            "id": str(p.id),
            "title": p.title or "",
            "summary": sanitize_summary_for_reuse(
                p.short_summary or "", max_chars=summary_chars
            ),
            "tags": [t.name for t in p.tags] if p.tags else [],
        }
        for p in papers
    ]
    papers_json = json.dumps(papers_payload)

    crossrefs_payload = [
        {
            "a": str(cr.paper_a),
            "b": str(cr.paper_b),
            "type": cr.relation_type,
            "strength": cr.strength,
            "description": sanitize_summary_for_reuse(
                cr.description or "", max_chars=300
            ),
        }
        for cr in crossrefs
    ]
    crossrefs_json = json.dumps(crossrefs_payload)

    max_detected = max((cr.detected_at for cr in crossrefs), default=None)
    context_hash = compute_context_hash(list(paper_ids), max_detected)
    valid_ids = {str(pid) for pid in paper_ids}

    return (
        existing_insights_json,
        papers_json,
        crossrefs_json,
        valid_ids,
        context_hash,
    )


async def generate_insights(
    db: AsyncSession, *, last_hash: str | None = None
) -> dict:
    """Orchestrate insight generation.

    Returns {"status", "hash", "insights_new", "insights_merged", "skipped"}.
    If `last_hash` matches the current context hash, skips the Claude call.
    """
    started = time.monotonic()
    (
        existing_json,
        papers_json,
        crossrefs_json,
        valid_paper_ids,
        context_hash,
    ) = await _build_generation_context(db)

    if not valid_paper_ids:
        logger.info(
            "insight_generation_skipped_empty",
            extra={"hash": context_hash},
        )
        return {
            "status": "skipped",
            "hash": context_hash,
            "insights_new": 0,
            "insights_merged": 0,
            "skipped": True,
        }

    if last_hash == context_hash:
        logger.info(
            "insight_generation_skipped_idempotent",
            extra={"hash": context_hash},
        )
        return {
            "status": "skipped",
            "hash": context_hash,
            "insights_new": 0,
            "insights_merged": 0,
            "skipped": True,
        }

    outputs = await generate_insights_from_claude(
        existing_insights_json=existing_json,
        papers_json=papers_json,
        crossrefs_json=crossrefs_json,
        valid_paper_ids=valid_paper_ids,
    )

    inserted = 0
    merged = 0
    # Cache existing insights per-type so we don't refetch on every iteration.
    existing_by_type_cache: dict[str, list[Insight]] = {}

    for new in outputs:
        if new.type not in existing_by_type_cache:
            rows = (
                await db.execute(
                    select(Insight).where(Insight.type == new.type)
                )
            ).scalars().all()
            existing_by_type_cache[new.type] = list(rows)

        action, persisted = await _dedup_and_persist(
            db, new, existing_by_type_cache[new.type]
        )
        if action == "inserted":
            inserted += 1
            # Make future dedup-lookups see the freshly inserted insight
            existing_by_type_cache[new.type].append(persisted)
        else:
            merged += 1

    await db.commit()

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "insight_generation_completed",
        extra={
            "insights_new": inserted,
            "insights_merged": merged,
            "duration_ms": duration_ms,
            "hash": context_hash,
        },
    )

    return {
        "status": "generated",
        "hash": context_hash,
        "insights_new": inserted,
        "insights_merged": merged,
        "skipped": False,
    }
