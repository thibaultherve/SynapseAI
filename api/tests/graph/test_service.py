"""Tests T13-T17: graph service (global + ego network + filters)."""

from datetime import date

import pytest

from app.core.enums import ReferenceStrength, RelationType
from app.graph import service
from app.graph.exceptions import GraphTooLargeError
from app.graph.schemas import GraphFilters
from app.papers.models import PaperTag
from app.tags.models import Tag


def _filters(**kwargs) -> GraphFilters:
    return GraphFilters(**kwargs)


@pytest.mark.asyncio
async def test_build_graph_returns_nodes_and_edges(
    db, paper_factory, crossref_factory
):
    """T13: build_graph returns all papers as nodes and all crossrefs as edges."""
    p1 = await paper_factory(title="A", authors_short="Doe et al.")
    p2 = await paper_factory(title="B", authors_short="Roe et al.")
    p3 = await paper_factory(title="C")
    await crossref_factory(p1.id, p2.id, relation_type="supports", strength="strong")
    await crossref_factory(p2.id, p3.id, relation_type="thematic", strength="weak")

    graph = await service.build_graph(db, _filters())

    assert graph.node_count == 3
    assert graph.edge_count == 2
    assert {n.id for n in graph.nodes} == {p1.id, p2.id, p3.id}

    # Degree is computed: p2 is in both edges
    degrees = {n.id: n.degree for n in graph.nodes}
    assert degrees[p2.id] == 2
    assert degrees[p1.id] == 1
    assert degrees[p3.id] == 1


@pytest.mark.asyncio
async def test_build_graph_raises_when_nodes_exceed_clamp(
    monkeypatch, db, paper_factory
):
    """T14: GRAPH_MAX_NODES clamp raises GraphTooLargeError."""
    monkeypatch.setattr("app.graph.service.graph_settings.GRAPH_MAX_NODES", 2)

    await paper_factory(title="A")
    await paper_factory(title="B")
    await paper_factory(title="C")

    with pytest.raises(GraphTooLargeError) as exc_info:
        await service.build_graph(db, _filters())
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_build_graph_applies_filters(db, paper_factory, crossref_factory):
    """T15: filters by tags / relation_type / min_strength / dates."""
    p_tag_a = await paper_factory(title="Tagged A", publication_date=date(2024, 6, 1))
    p_tag_b = await paper_factory(title="Tagged B", publication_date=date(2024, 6, 1))
    p_untagged = await paper_factory(title="Untagged", publication_date=date(2024, 6, 1))
    p_old = await paper_factory(title="Old", publication_date=date(2020, 1, 1))

    tag = Tag(name="ai", category="sub_domain")
    db.add(tag)
    await db.flush()
    db.add_all([
        PaperTag(paper_id=p_tag_a.id, tag_id=tag.id),
        PaperTag(paper_id=p_tag_b.id, tag_id=tag.id),
    ])
    await db.commit()

    await crossref_factory(
        p_tag_a.id, p_tag_b.id, relation_type="supports", strength="strong"
    )
    await crossref_factory(
        p_tag_a.id, p_untagged.id, relation_type="thematic", strength="weak"
    )

    # tags filter: only keep p_tag_a + p_tag_b; edge between them kept,
    # edge to p_untagged dropped because p_untagged is not in node set
    graph = await service.build_graph(db, _filters(tags=[tag.id]))
    assert {n.id for n in graph.nodes} == {p_tag_a.id, p_tag_b.id}
    assert graph.edge_count == 1
    assert graph.edges[0].relation_type == RelationType.SUPPORTS

    # relation_type filter
    graph = await service.build_graph(
        db, _filters(relation_type=RelationType.THEMATIC)
    )
    assert graph.edge_count == 1
    assert graph.edges[0].relation_type == RelationType.THEMATIC

    # min_strength=strong drops the weak edge
    graph = await service.build_graph(
        db, _filters(min_strength=ReferenceStrength.STRONG)
    )
    assert graph.edge_count == 1
    assert graph.edges[0].strength == ReferenceStrength.STRONG

    # date filter excludes the old paper
    graph = await service.build_graph(db, _filters(date_from=date(2024, 1, 1)))
    assert p_old.id not in {n.id for n in graph.nodes}


@pytest.mark.asyncio
async def test_build_ego_network_depth_1(db, paper_factory, crossref_factory):
    """T16: depth=1 returns direct neighbors only."""
    center = await paper_factory(title="Center")
    n1 = await paper_factory(title="N1")
    n2 = await paper_factory(title="N2")
    far = await paper_factory(title="Far")

    await crossref_factory(center.id, n1.id, strength="strong")
    await crossref_factory(center.id, n2.id, strength="moderate")
    # Far is a neighbor of n1, NOT of center (2 hops)
    await crossref_factory(n1.id, far.id, strength="weak")

    graph = await service.build_ego_network(db, center.id, depth=1, filters=_filters())

    node_ids = {n.id for n in graph.nodes}
    assert center.id in node_ids
    assert n1.id in node_ids
    assert n2.id in node_ids
    assert far.id not in node_ids


@pytest.mark.asyncio
async def test_build_ego_network_depth_3(db, paper_factory, crossref_factory):
    """T17: depth=3 reaches 3 hops."""
    p0 = await paper_factory(title="P0")
    p1 = await paper_factory(title="P1")
    p2 = await paper_factory(title="P2")
    p3 = await paper_factory(title="P3")
    p_out = await paper_factory(title="P_out")  # 4 hops, excluded

    await crossref_factory(p0.id, p1.id, strength="moderate")
    await crossref_factory(p1.id, p2.id, strength="moderate")
    await crossref_factory(p2.id, p3.id, strength="moderate")
    await crossref_factory(p3.id, p_out.id, strength="moderate")

    graph = await service.build_ego_network(db, p0.id, depth=3, filters=_filters())
    node_ids = {n.id for n in graph.nodes}
    assert {p0.id, p1.id, p2.id, p3.id}.issubset(node_ids)
    assert p_out.id not in node_ids


@pytest.mark.asyncio
async def test_build_ego_network_honors_edge_filters(
    db, paper_factory, crossref_factory
):
    """Edge filters prune the BFS: weak edge is NOT traversed when min_strength=strong."""
    center = await paper_factory(title="Center")
    reachable = await paper_factory(title="Reachable")
    blocked = await paper_factory(title="Blocked")

    await crossref_factory(center.id, reachable.id, strength="strong")
    await crossref_factory(reachable.id, blocked.id, strength="weak")

    graph = await service.build_ego_network(
        db,
        center.id,
        depth=3,
        filters=_filters(min_strength=ReferenceStrength.STRONG),
    )
    node_ids = {n.id for n in graph.nodes}
    assert center.id in node_ids
    assert reachable.id in node_ids
    assert blocked.id not in node_ids


@pytest.mark.asyncio
async def test_compute_graph_etag_changes_with_data(
    db, paper_factory, crossref_factory
):
    """ETag is stable when data is stable, changes when a crossref is added."""
    p1 = await paper_factory(title="A")
    p2 = await paper_factory(title="B")

    etag1 = await service.compute_graph_etag(db, _filters())
    etag2 = await service.compute_graph_etag(db, _filters())
    assert etag1 == etag2

    await crossref_factory(p1.id, p2.id)
    etag3 = await service.compute_graph_etag(db, _filters())
    assert etag3 != etag1


@pytest.mark.asyncio
async def test_compute_graph_etag_differs_by_filters(db, paper_factory):
    """Different filter values produce different ETags."""
    await paper_factory(title="A")

    etag_no_filter = await service.compute_graph_etag(db, _filters())
    etag_with_rel = await service.compute_graph_etag(
        db, _filters(relation_type=RelationType.SUPPORTS)
    )
    assert etag_no_filter != etag_with_rel
