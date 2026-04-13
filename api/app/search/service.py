"""Search service: full-text search (FTS) and semantic search via pgvector."""

import logging
import uuid

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.papers.models import Paper, PaperTag
from app.processing.embedding_service import encode_text
from app.processing.models import PaperEmbedding
from app.search.schemas import SearchFilters, SearchResultItem
from app.tags.models import Tag

logger = logging.getLogger(__name__)


async def full_text_search(
    db: AsyncSession,
    query: str,
    limit: int = 20,
    offset: int = 0,
    filters: SearchFilters | None = None,
) -> tuple[list[SearchResultItem], int]:
    """Full-text search using PostgreSQL websearch_to_tsquery."""
    ts_query = func.websearch_to_tsquery("english", query)

    # Base query: papers matching the FTS query
    base = (
        select(
            Paper.id,
            Paper.title,
            Paper.authors_short,
            Paper.journal,
            Paper.doi,
            Paper.short_summary,
            Paper.keywords,
            func.ts_rank(Paper.search_vector, ts_query).label("relevance_score"),
            func.ts_headline(
                "english",
                func.coalesce(Paper.short_summary, ""),
                ts_query,
                "MaxFragments=2, MaxWords=60, MinWords=20",
            ).label("snippet"),
        )
        .where(Paper.search_vector.op("@@")(ts_query))
    )

    base = _apply_filters(base, filters)

    # Count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Results
    results_q = base.order_by(literal_column("relevance_score").desc()).offset(offset).limit(limit)
    rows = (await db.execute(results_q)).all()

    paper_ids = [row.id for row in rows]
    tags_map = await _batch_get_paper_tags(db, paper_ids)
    items = [_row_to_item(row, tags_map.get(row.id, [])) for row in rows]
    return items, total


async def semantic_search(
    db: AsyncSession,
    query: str,
    limit: int = 20,
    offset: int = 0,
    filters: SearchFilters | None = None,
) -> tuple[list[SearchResultItem], int]:
    """Semantic search using pgvector cosine similarity.

    Strategy: encode query -> top-K chunks -> rank per paper -> pick best chunk.
    """
    query_vector = await encode_text(query)

    # Two-stage: fetch top-K chunks, then pick best per paper
    top_k = limit * 5
    chunk_subq = (
        select(
            PaperEmbedding.paper_id,
            PaperEmbedding.chunk_text,
            (1 - PaperEmbedding.embedding.cosine_distance(query_vector)).label("similarity"),
        )
        .order_by(PaperEmbedding.embedding.cosine_distance(query_vector))
        .limit(top_k)
        .subquery()
    )

    # Rank chunks per paper by similarity, pick the best one
    ranked = (
        select(
            chunk_subq.c.paper_id,
            chunk_subq.c.similarity,
            chunk_subq.c.chunk_text,
            func.row_number().over(
                partition_by=chunk_subq.c.paper_id,
                order_by=chunk_subq.c.similarity.desc(),
            ).label("rn"),
        )
        .subquery()
    )

    agg_subq = (
        select(
            ranked.c.paper_id,
            ranked.c.similarity.label("relevance_score"),
            ranked.c.chunk_text.label("best_chunk"),
        )
        .where(ranked.c.rn == 1)
        .subquery()
    )

    base = (
        select(
            Paper.id,
            Paper.title,
            Paper.authors_short,
            Paper.journal,
            Paper.doi,
            Paper.short_summary,
            Paper.keywords,
            agg_subq.c.relevance_score,
            agg_subq.c.best_chunk.label("snippet"),
        )
        .join(agg_subq, Paper.id == agg_subq.c.paper_id)
    )

    base = _apply_filters(base, filters)

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    results_q = base.order_by(literal_column("relevance_score").desc()).offset(offset).limit(limit)
    rows = (await db.execute(results_q)).all()

    paper_ids = [row.id for row in rows]
    tags_map = await _batch_get_paper_tags(db, paper_ids)
    items = [_row_to_item(row, tags_map.get(row.id, [])) for row in rows]
    return items, total


async def find_similar(
    db: AsyncSession,
    paper_id: uuid.UUID,
    limit: int = 10,
) -> list[SearchResultItem]:
    """Find papers similar to the given paper by averaging its embeddings."""
    # Compute average embedding for the paper
    avg_q = select(
        func.avg(PaperEmbedding.embedding).label("avg_embedding"),
    ).where(PaperEmbedding.paper_id == paper_id)

    avg_result = (await db.execute(avg_q)).first()
    if avg_result is None or avg_result.avg_embedding is None:
        return []

    avg_embedding = avg_result.avg_embedding

    # Find similar papers (exclude the source paper)
    chunk_subq = (
        select(
            PaperEmbedding.paper_id,
            func.max(
                1 - PaperEmbedding.embedding.cosine_distance(avg_embedding)
            ).label("similarity"),
        )
        .where(PaperEmbedding.paper_id != paper_id)
        .group_by(PaperEmbedding.paper_id)
        .subquery()
    )

    base = (
        select(
            Paper.id,
            Paper.title,
            Paper.authors_short,
            Paper.journal,
            Paper.doi,
            Paper.short_summary,
            Paper.keywords,
            chunk_subq.c.similarity.label("relevance_score"),
        )
        .join(chunk_subq, Paper.id == chunk_subq.c.paper_id)
        .order_by(literal_column("relevance_score").desc())
        .limit(limit)
    )

    rows = (await db.execute(base)).all()

    paper_ids = [row.id for row in rows]
    tags_map = await _batch_get_paper_tags(db, paper_ids)
    return [_row_to_item(row, tags_map.get(row.id, [])) for row in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_filters(query, filters: SearchFilters | None):
    """Apply tag and date filters to a query."""
    if filters is None:
        return query

    if filters.tags:
        for tag_id in filters.tags:
            query = query.where(
                Paper.id.in_(
                    select(PaperTag.paper_id).where(PaperTag.tag_id == tag_id)
                )
            )

    if filters.date_from:
        query = query.where(Paper.publication_date >= filters.date_from)

    if filters.date_to:
        query = query.where(Paper.publication_date <= filters.date_to)

    return query


async def _batch_get_paper_tags(
    db: AsyncSession, paper_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Get tag names for multiple papers in a single query."""
    if not paper_ids:
        return {}
    result = await db.execute(
        select(PaperTag.paper_id, Tag.name)
        .join(Tag, Tag.id == PaperTag.tag_id)
        .where(PaperTag.paper_id.in_(paper_ids))
    )
    tags_by_paper: dict[uuid.UUID, list[str]] = {}
    for paper_id, tag_name in result.all():
        tags_by_paper.setdefault(paper_id, []).append(tag_name)
    return tags_by_paper


def _row_to_item(row, tags: list[str]) -> SearchResultItem:
    """Convert a query row to a SearchResultItem."""
    snippet = row.snippet if hasattr(row, "snippet") and row.snippet else None
    if snippet and len(snippet) > 500:
        snippet = snippet[:500] + "..."

    return SearchResultItem(
        id=row.id,
        title=row.title,
        authors_short=row.authors_short,
        journal=row.journal,
        doi=row.doi,
        short_summary=row.short_summary,
        keywords=row.keywords,
        snippet=snippet,
        relevance_score=float(row.relevance_score),
        tags=tags,
    )
