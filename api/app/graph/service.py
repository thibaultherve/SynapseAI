import hashlib
import uuid

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import graph_settings
from app.graph.exceptions import GraphTooLargeError
from app.graph.schemas import EdgeResponse, GraphData, GraphFilters, NodeResponse
from app.papers.models import Paper, PaperTag
from app.processing.models import CrossReference

_STRENGTH_ORDER = {"weak": 0, "moderate": 1, "strong": 2}


def _strength_allowlist(min_strength: str | None) -> list[str] | None:
    if not min_strength:
        return None
    threshold = _STRENGTH_ORDER.get(min_strength, 0)
    return [k for k, v in _STRENGTH_ORDER.items() if v >= threshold]


def _edge_filters(filters: GraphFilters):
    """Return a list of SQLAlchemy clauses to apply on CrossReference."""
    clauses = []
    if filters.relation_type:
        clauses.append(CrossReference.relation_type == filters.relation_type.value)
    allowed = _strength_allowlist(
        filters.min_strength.value if filters.min_strength else None
    )
    if allowed:
        clauses.append(CrossReference.strength.in_(allowed))
    return clauses


def _paper_filters(filters: GraphFilters):
    """Filters applied to paper selection (tags + publication date)."""
    clauses = []
    if filters.date_from:
        clauses.append(Paper.publication_date >= filters.date_from)
    if filters.date_to:
        clauses.append(Paper.publication_date <= filters.date_to)
    return clauses


async def _paper_ids_matching_tags(
    db: AsyncSession, tag_ids: list[int]
) -> set[uuid.UUID]:
    rows = await db.execute(
        select(PaperTag.paper_id).where(PaperTag.tag_id.in_(tag_ids)).distinct()
    )
    return {row[0] for row in rows.all()}


async def build_graph(db: AsyncSession, filters: GraphFilters) -> GraphData:
    """Build a global graph (all papers + crossrefs) with optional filters.

    Raises GraphTooLargeError if nodes or edges exceed clamps.
    """
    paper_q = select(Paper)
    for clause in _paper_filters(filters):
        paper_q = paper_q.where(clause)
    if filters.tags:
        tagged_ids = await _paper_ids_matching_tags(db, filters.tags)
        if not tagged_ids:
            return GraphData(nodes=[], edges=[], node_count=0, edge_count=0)
        paper_q = paper_q.where(Paper.id.in_(tagged_ids))

    papers = (await db.execute(paper_q)).scalars().unique().all()
    paper_ids = {p.id for p in papers}
    node_count = len(papers)

    if node_count > graph_settings.GRAPH_MAX_NODES:
        raise GraphTooLargeError(node_count=node_count, edge_count=0)

    edge_q = select(CrossReference).where(
        and_(
            CrossReference.paper_a.in_(paper_ids),
            CrossReference.paper_b.in_(paper_ids),
        )
    )
    for clause in _edge_filters(filters):
        edge_q = edge_q.where(clause)

    edges_rows = (await db.execute(edge_q)).scalars().all() if paper_ids else []
    edge_count = len(edges_rows)

    if edge_count > graph_settings.GRAPH_MAX_EDGES:
        raise GraphTooLargeError(node_count=node_count, edge_count=edge_count)

    degree: dict[uuid.UUID, int] = {pid: 0 for pid in paper_ids}
    for e in edges_rows:
        degree[e.paper_a] += 1
        degree[e.paper_b] += 1

    nodes = [
        NodeResponse(
            id=p.id,
            title=p.title,
            authors_short=p.authors_short,
            tags=[t.name for t in p.tags],
            degree=degree[p.id],
        )
        for p in papers
    ]
    edges = [
        EdgeResponse(
            source=e.paper_a,
            target=e.paper_b,
            relation_type=e.relation_type,
            strength=e.strength,
            description=e.description,
        )
        for e in edges_rows
    ]

    return GraphData(
        nodes=nodes,
        edges=edges,
        node_count=node_count,
        edge_count=edge_count,
        truncated=False,
    )


async def build_ego_network(
    db: AsyncSession,
    paper_id: uuid.UUID,
    depth: int,
    filters: GraphFilters,
) -> GraphData:
    """Return an ego network centered on `paper_id`, BFS up to `depth` hops.

    Uses a recursive CTE over cross_reference, honoring edge filters.
    `depth` is clamped to [1, GRAPH_EGO_MAX_DEPTH] by the caller.
    """
    extra_edge_sql = ""
    params: dict = {"root": paper_id, "max_depth": depth}

    if filters.relation_type:
        extra_edge_sql += " AND cr.relation_type = :relation_type"
        params["relation_type"] = filters.relation_type.value

    allowed = _strength_allowlist(
        filters.min_strength.value if filters.min_strength else None
    )
    if allowed:
        extra_edge_sql += " AND cr.strength = ANY(:allowed_strengths)"
        params["allowed_strengths"] = allowed

    cte_sql = f"""
    WITH RECURSIVE ego(paper_id, depth) AS (
        SELECT CAST(:root AS uuid), 0
        UNION
        SELECT CASE WHEN cr.paper_a = e.paper_id THEN cr.paper_b ELSE cr.paper_a END,
               e.depth + 1
        FROM ego e
        JOIN cross_reference cr
          ON (cr.paper_a = e.paper_id OR cr.paper_b = e.paper_id)
        WHERE e.depth < :max_depth
          {extra_edge_sql}
    )
    SELECT DISTINCT paper_id FROM ego
    """

    result = await db.execute(text(cte_sql), params)
    reachable_ids = {row[0] for row in result.all()}

    if not reachable_ids:
        return GraphData(nodes=[], edges=[], node_count=0, edge_count=0)

    # Apply paper-level filters (date) on top of reachable set
    paper_q = select(Paper).where(Paper.id.in_(reachable_ids))
    for clause in _paper_filters(filters):
        paper_q = paper_q.where(clause)
    if filters.tags:
        tagged_ids = await _paper_ids_matching_tags(db, filters.tags)
        # Always keep the root even if it does not match tags
        tagged_ids.add(paper_id)
        paper_q = paper_q.where(Paper.id.in_(tagged_ids))

    papers = (await db.execute(paper_q)).scalars().unique().all()
    kept_ids = {p.id for p in papers}

    edge_q = select(CrossReference).where(
        and_(
            CrossReference.paper_a.in_(kept_ids),
            CrossReference.paper_b.in_(kept_ids),
        )
    )
    for clause in _edge_filters(filters):
        edge_q = edge_q.where(clause)
    edges_rows = (await db.execute(edge_q)).scalars().all() if kept_ids else []

    degree: dict[uuid.UUID, int] = {pid: 0 for pid in kept_ids}
    for e in edges_rows:
        degree[e.paper_a] += 1
        degree[e.paper_b] += 1

    nodes = [
        NodeResponse(
            id=p.id,
            title=p.title,
            authors_short=p.authors_short,
            tags=[t.name for t in p.tags],
            degree=degree[p.id],
        )
        for p in papers
    ]
    edges = [
        EdgeResponse(
            source=e.paper_a,
            target=e.paper_b,
            relation_type=e.relation_type,
            strength=e.strength,
            description=e.description,
        )
        for e in edges_rows
    ]

    return GraphData(
        nodes=nodes,
        edges=edges,
        node_count=len(nodes),
        edge_count=len(edges),
        truncated=False,
    )


async def compute_graph_etag(db: AsyncSession, filters: GraphFilters) -> str:
    """Compute a weak ETag from paper/crossref/tag mutation timestamps and filters.

    Components:
    - MAX(cross_reference.detected_at) — invalidates on new/retracted edges
    - MAX(paper.updated_at) — invalidates on paper mutations (incl. tag changes
      which touch paper.updated_at via ORM onupdate)
    - COUNT(paper) — invalidates on paper creation/deletion
    - filters — distinct ETags per filter combination
    """
    max_detected = (
        await db.execute(select(func.max(CrossReference.detected_at)))
    ).scalar()
    max_paper_updated = (
        await db.execute(select(func.max(Paper.updated_at)))
    ).scalar()
    paper_count = (await db.execute(select(func.count(Paper.id)))).scalar() or 0

    filter_key = "|".join([
        ",".join(str(t) for t in sorted(filters.tags)) if filters.tags else "",
        filters.relation_type.value if filters.relation_type else "",
        filters.min_strength.value if filters.min_strength else "",
        filters.date_from.isoformat() if filters.date_from else "",
        filters.date_to.isoformat() if filters.date_to else "",
    ])

    raw = (
        f"{max_detected.isoformat() if max_detected else ''}|"
        f"{max_paper_updated.isoformat() if max_paper_updated else ''}|"
        f"{paper_count}|{filter_key}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
