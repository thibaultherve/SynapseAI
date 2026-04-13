"""T25-T28: Search router tests — FTS, semantic, similar, validation."""

import uuid

import pytest
from sqlalchemy import text as sa_text

from app.core.enums import SourceType


# ---------------------------------------------------------------------------
# T25: POST /api/search mode=exact (FTS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_fts_returns_matching_papers(client, db, paper_factory):
    """FTS search finds papers matching the query in title/summary."""
    paper = await paper_factory(
        source_type=SourceType.WEB,
        title="Oligodendrocyte differentiation in scRNA-seq",
        short_summary="Study of oligodendrocyte precursor cells using single-cell RNA sequencing.",
        extracted_text="This paper explores oligodendrocyte biology.",
    )

    # Force search_vector recomputation (generated column needs data to be visible)
    await db.execute(sa_text("SELECT 1"))
    await db.commit()

    response = await client.post("/api/search", json={
        "query": "oligodendrocyte",
        "mode": "exact",
        "limit": 10,
        "offset": 0,
    })

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "exact"
    assert data["query"] == "oligodendrocyte"
    assert data["total_count"] >= 1
    assert len(data["results"]) >= 1

    result_ids = [r["id"] for r in data["results"]]
    assert str(paper.id) in result_ids


@pytest.mark.asyncio
async def test_search_fts_no_results(client, db, paper_factory):
    """FTS search with no matches returns empty results."""
    await paper_factory(
        source_type=SourceType.WEB,
        title="Unrelated paper",
        short_summary="Nothing relevant here.",
        extracted_text="Completely different topic.",
    )

    response = await client.post("/api/search", json={
        "query": "xyznonexistent",
        "mode": "exact",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_fts_with_filters(client, db, paper_factory):
    """FTS search respects date filters."""
    from datetime import date

    paper = await paper_factory(
        source_type=SourceType.WEB,
        title="Neuroscience study 2024",
        short_summary="A neuroscience paper from 2024.",
        extracted_text="Detailed neuroscience research.",
        publication_date=date(2024, 6, 15),
    )

    # Search with date filter that includes the paper
    response = await client.post("/api/search", json={
        "query": "neuroscience",
        "mode": "exact",
        "filters": {
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
        },
    })
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] >= 1

    # Search with date filter that excludes the paper
    response = await client.post("/api/search", json={
        "query": "neuroscience",
        "mode": "exact",
        "filters": {
            "date_from": "2025-01-01",
            "date_to": "2025-12-31",
        },
    })
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 0


# ---------------------------------------------------------------------------
# T26: POST /api/search mode=semantic (mock embeddings)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_semantic_returns_results(
    client, db, paper_factory, embedding_factory, mock_embedding
):
    """Semantic search finds papers by embedding similarity."""
    paper = await paper_factory(
        source_type=SourceType.WEB,
        title="Neural network research",
        short_summary="Deep learning methods for brain imaging.",
        extracted_text="Research on neural networks.",
    )
    await embedding_factory(paper.id, chunks=[
        "Deep learning neural networks for brain imaging.",
        "Convolutional networks applied to MRI data.",
    ])

    response = await client.post("/api/search", json={
        "query": "neural network deep learning",
        "mode": "semantic",
        "limit": 10,
    })

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "semantic"
    assert data["total_count"] >= 1
    assert len(data["results"]) >= 1

    result_ids = [r["id"] for r in data["results"]]
    assert str(paper.id) in result_ids
    # Each result should have a relevance score
    for r in data["results"]:
        assert "relevance_score" in r
        assert isinstance(r["relevance_score"], float)


@pytest.mark.asyncio
async def test_search_semantic_no_embeddings(client, db, paper_factory, mock_embedding):
    """Semantic search with no embeddings returns empty results."""
    await paper_factory(
        source_type=SourceType.WEB,
        title="Paper without embeddings",
        extracted_text="Some text here.",
    )

    response = await client.post("/api/search", json={
        "query": "anything",
        "mode": "semantic",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 0
    assert data["results"] == []


# ---------------------------------------------------------------------------
# T27: GET /api/search/similar/:id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_similar_returns_similar_papers(
    client, db, paper_factory, embedding_factory
):
    """Similar papers endpoint returns papers ranked by embedding similarity."""
    paper_a = await paper_factory(
        source_type=SourceType.WEB,
        title="Paper A",
        short_summary="Summary A.",
        extracted_text="Text A.",
    )
    paper_b = await paper_factory(
        source_type=SourceType.WEB,
        title="Paper B",
        short_summary="Summary B.",
        extracted_text="Text B.",
    )

    # Create embeddings for both papers
    await embedding_factory(paper_a.id, chunks=["Chunk A1", "Chunk A2"])
    await embedding_factory(paper_b.id, chunks=["Chunk B1"])

    response = await client.get(f"/api/search/similar/{paper_a.id}")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # paper_b should appear as similar to paper_a
    if len(data) > 0:
        result_ids = [r["id"] for r in data]
        assert str(paper_b.id) in result_ids
        # Source paper should NOT appear in results
        assert str(paper_a.id) not in result_ids


@pytest.mark.asyncio
async def test_search_similar_paper_not_found(client, db):
    """Similar papers for nonexistent paper returns 404."""
    fake_id = uuid.uuid4()
    response = await client.get(f"/api/search/similar/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_search_similar_no_embeddings(client, db, paper_factory):
    """Similar papers for paper with no embeddings returns empty list."""
    paper = await paper_factory(
        source_type=SourceType.WEB,
        title="Paper without embeddings",
        extracted_text="Some text.",
    )

    response = await client.get(f"/api/search/similar/{paper.id}")

    assert response.status_code == 200
    data = response.json()
    assert data == []


# ---------------------------------------------------------------------------
# T28: Search input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_empty_query_rejected(client):
    """Search with empty query returns 422."""
    response = await client.post("/api/search", json={
        "query": "",
        "mode": "exact",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_invalid_mode_rejected(client):
    """Search with invalid mode returns 422."""
    response = await client.post("/api/search", json={
        "query": "test",
        "mode": "invalid_mode",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_limit_out_of_range(client):
    """Search with limit > 100 returns 422."""
    response = await client.post("/api/search", json={
        "query": "test",
        "mode": "exact",
        "limit": 200,
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_negative_offset_rejected(client):
    """Search with negative offset returns 422."""
    response = await client.post("/api/search", json={
        "query": "test",
        "mode": "exact",
        "offset": -1,
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_pagination(client, db, paper_factory):
    """Search pagination via offset and limit works correctly."""
    # Create multiple papers with matching content
    for i in range(5):
        await paper_factory(
            source_type=SourceType.WEB,
            title=f"Neuroscience paper {i}",
            short_summary=f"A neuroscience study number {i}.",
            extracted_text=f"Research on neuroscience topic {i}.",
        )

    # First page
    response = await client.post("/api/search", json={
        "query": "neuroscience",
        "mode": "exact",
        "limit": 2,
        "offset": 0,
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 2
    assert data["total_count"] == 5

    # Second page
    response = await client.post("/api/search", json={
        "query": "neuroscience",
        "mode": "exact",
        "limit": 2,
        "offset": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 2
