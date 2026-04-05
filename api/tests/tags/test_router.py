import pytest

from app.papers.models import PaperTag
from app.tags.models import Tag


# --- Fixtures ---


@pytest.fixture
async def tag_factory(db):
    """Factory to create test tags (committed to DB for client visibility)."""

    async def _create(
        name: str = "neural-network",
        category: str = "technique",
        description: str | None = None,
    ) -> Tag:
        tag = Tag(name=name, category=category, description=description)
        db.add(tag)
        await db.flush()
        await db.refresh(tag)
        return tag

    return _create


@pytest.fixture
async def sample_tags(db, tag_factory):
    """Create a set of tags across categories, committed to DB."""
    tags = {
        "cnn": await tag_factory("CNN", "technique", "Convolutional Neural Network"),
        "rnn": await tag_factory("RNN", "technique", "Recurrent Neural Network"),
        "ms": await tag_factory("Multiple Sclerosis", "pathology"),
        "neuro": await tag_factory("Neuroscience", "sub_domain"),
        "imaging": await tag_factory("Brain Imaging", "topic"),
    }
    await db.commit()
    return tags


# --- T9: GET /api/tags — tags grouped by category ---


@pytest.mark.asyncio
async def test_list_tags_grouped(client, sample_tags):
    """GET /api/tags returns tags grouped by category."""
    response = await client.get("/api/tags")

    assert response.status_code == 200
    data = response.json()
    assert "technique" in data
    assert "pathology" in data
    assert "sub_domain" in data
    assert "topic" in data
    technique_names = [t["name"] for t in data["technique"]]
    assert "CNN" in technique_names
    assert "RNN" in technique_names


@pytest.mark.asyncio
async def test_list_tags_filter_category(client, sample_tags):
    """GET /api/tags?category=technique returns only technique tags."""
    response = await client.get("/api/tags?category=technique")

    assert response.status_code == 200
    data = response.json()
    assert "technique" in data
    assert "pathology" not in data


@pytest.mark.asyncio
async def test_list_tags_empty(client):
    """GET /api/tags with no tags returns empty dict."""
    response = await client.get("/api/tags")

    assert response.status_code == 200
    assert response.json() == {}


# --- T10: GET /api/tags/:id/papers — papers with this tag ---


@pytest.mark.asyncio
async def test_get_tag_papers(client, db, sample_tags, paper_factory):
    """GET /api/tags/:id/papers returns papers associated with the tag."""
    paper = await paper_factory(title="Tagged Paper")
    tag = sample_tags["cnn"]

    db.add(PaperTag(paper_id=paper.id, tag_id=tag.id))
    await db.commit()

    response = await client.get(f"/api/tags/{tag.id}/papers")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Tagged Paper"


@pytest.mark.asyncio
async def test_get_tag_papers_not_found(client):
    """GET /api/tags/99999/papers with unknown tag -> 404."""
    response = await client.get("/api/tags/99999/papers")

    assert response.status_code == 404
    assert "TAG_NOT_FOUND" in response.json()["error"]["code"]


# --- T11: PATCH /api/tags/:id — rename ---


@pytest.mark.asyncio
async def test_rename_tag_valid(client, sample_tags):
    """PATCH /api/tags/:id with valid name -> updated tag."""
    tag = sample_tags["cnn"]

    response = await client.patch(
        f"/api/tags/{tag.id}",
        json={"name": "ConvNet"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "ConvNet"
    assert data["category"] == "technique"


@pytest.mark.asyncio
async def test_rename_tag_invalid_regex(client, sample_tags):
    """PATCH /api/tags/:id with invalid characters -> 422."""
    tag = sample_tags["cnn"]

    response = await client.patch(
        f"/api/tags/{tag.id}",
        json={"name": "<script>alert('xss')</script>"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rename_tag_duplicate(client, sample_tags):
    """PATCH /api/tags/:id to an existing name+category -> 409."""
    tag = sample_tags["cnn"]

    response = await client.patch(
        f"/api/tags/{tag.id}",
        json={"name": "RNN"},  # RNN already exists in technique
    )

    assert response.status_code == 409
    assert "DUPLICATE_TAG" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_rename_tag_not_found(client):
    """PATCH /api/tags/99999 -> 404."""
    response = await client.patch(
        "/api/tags/99999",
        json={"name": "Anything"},
    )

    assert response.status_code == 404


# --- T12: DELETE /api/tags/:id — cascade paper_tags ---


@pytest.mark.asyncio
async def test_delete_tag(client, sample_tags):
    """DELETE /api/tags/:id -> 204."""
    tag = sample_tags["cnn"]

    response = await client.delete(f"/api/tags/{tag.id}")

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_tag_cascades_paper_tags(client, db, sample_tags, paper_factory):
    """DELETE /api/tags/:id removes paper_tag associations."""
    paper = await paper_factory()
    tag = sample_tags["cnn"]

    db.add(PaperTag(paper_id=paper.id, tag_id=tag.id))
    await db.commit()

    response = await client.delete(f"/api/tags/{tag.id}")
    assert response.status_code == 204

    # Paper should still exist
    response = await client.get(f"/api/papers/{paper.id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_delete_tag_not_found(client):
    """DELETE /api/tags/99999 -> 404."""
    response = await client.delete("/api/tags/99999")

    assert response.status_code == 404


# --- T13: POST /api/tags/merge — merge OK ---


@pytest.mark.asyncio
async def test_merge_tags(client, db, sample_tags, paper_factory):
    """POST /api/tags/merge moves associations from source to target."""
    paper = await paper_factory()
    source = sample_tags["cnn"]
    target = sample_tags["rnn"]

    db.add(PaperTag(paper_id=paper.id, tag_id=source.id))
    await db.commit()

    response = await client.post(
        "/api/tags/merge",
        json={"source_id": source.id, "target_id": target.id},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == target.id
    assert data["name"] == "RNN"


@pytest.mark.asyncio
async def test_merge_tags_on_conflict(client, db, sample_tags, paper_factory):
    """POST /api/tags/merge with paper already on target -> no duplicate, 200."""
    paper = await paper_factory()
    source = sample_tags["cnn"]
    target = sample_tags["rnn"]

    db.add(PaperTag(paper_id=paper.id, tag_id=source.id))
    db.add(PaperTag(paper_id=paper.id, tag_id=target.id))
    await db.commit()

    response = await client.post(
        "/api/tags/merge",
        json={"source_id": source.id, "target_id": target.id},
    )

    assert response.status_code == 200


# --- T14: POST /api/tags/merge — source == target -> 422 ---


@pytest.mark.asyncio
async def test_merge_tags_self(client, sample_tags):
    """POST /api/tags/merge with source == target -> 422."""
    tag = sample_tags["cnn"]

    response = await client.post(
        "/api/tags/merge",
        json={"source_id": tag.id, "target_id": tag.id},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_merge_tags_source_not_found(client, sample_tags):
    """POST /api/tags/merge with non-existent source -> 404."""
    target = sample_tags["cnn"]

    response = await client.post(
        "/api/tags/merge",
        json={"source_id": 99999, "target_id": target.id},
    )

    assert response.status_code == 404
