"""Tests T18-T20: graph router (ETag, 413, depth clamp)."""

import uuid

import pytest


@pytest.mark.asyncio
async def test_get_graph_returns_nodes_and_edges(
    client, paper_factory, crossref_factory
):
    p1 = await paper_factory(title="A")
    p2 = await paper_factory(title="B")
    await crossref_factory(
        p1.id, p2.id, relation_type="supports", strength="strong"
    )

    resp = await client.get("/api/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_count"] == 2
    assert data["edge_count"] == 1
    assert resp.headers.get("etag")


@pytest.mark.asyncio
async def test_get_graph_etag_returns_304(client, paper_factory, crossref_factory):
    """T18: second request with matching If-None-Match returns 304."""
    p1 = await paper_factory(title="A")
    p2 = await paper_factory(title="B")
    await crossref_factory(p1.id, p2.id)

    first = await client.get("/api/graph")
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag

    second = await client.get("/api/graph", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers.get("etag") == etag


@pytest.mark.asyncio
async def test_get_graph_returns_413_when_too_large(
    client, monkeypatch, paper_factory
):
    """T19: 413 GRAPH_TOO_LARGE when nodes exceed clamp."""
    monkeypatch.setattr("app.graph.service.graph_settings.GRAPH_MAX_NODES", 1)

    await paper_factory(title="A")
    await paper_factory(title="B")

    resp = await client.get("/api/graph")
    assert resp.status_code == 413
    body = resp.json()
    assert body["error"]["code"] == "GRAPH_TOO_LARGE"
    assert "suggestion" in body["error"]["message"].lower() or "filter" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_ego_network_depth_clamp_rejects_4(client, paper_factory):
    """T20: depth=4 returns 422 (FastAPI Query le=3)."""
    paper = await paper_factory(title="Root")
    resp = await client.get(f"/api/graph/paper/{paper.id}?depth=4")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ego_network_depth_clamp_rejects_0(client, paper_factory):
    paper = await paper_factory(title="Root")
    resp = await client.get(f"/api/graph/paper/{paper.id}?depth=0")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ego_network_unknown_paper_returns_404(client):
    fake = uuid.uuid4()
    resp = await client.get(f"/api/graph/paper/{fake}?depth=1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ego_network_returns_neighbors(
    client, paper_factory, crossref_factory
):
    center = await paper_factory(title="Center")
    neighbor = await paper_factory(title="Neighbor")
    await crossref_factory(center.id, neighbor.id, strength="strong")

    resp = await client.get(f"/api/graph/paper/{center.id}?depth=1")
    assert resp.status_code == 200
    data = resp.json()
    ids = {n["id"] for n in data["nodes"]}
    assert str(center.id) in ids
    assert str(neighbor.id) in ids
    assert data["edge_count"] == 1
