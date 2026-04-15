"""Tests T11-T12: GET /api/papers/:id/crossrefs endpoint."""

import uuid

import pytest


@pytest.mark.asyncio
async def test_get_crossrefs_hydrates_other_paper(
    client, paper_factory, crossref_factory
):
    """T11: GET /api/papers/:id/crossrefs returns the OTHER paper, hydrated."""
    p_ref = await paper_factory(title="Reference", short_summary="A")
    p_other1 = await paper_factory(title="Other One", short_summary="B")
    p_other2 = await paper_factory(title="Other Two", short_summary="C")

    await crossref_factory(
        p_ref.id, p_other1.id, relation_type="supports", strength="strong"
    )
    await crossref_factory(
        p_ref.id, p_other2.id, relation_type="extends", strength="moderate"
    )

    response = await client.get(f"/api/papers/{p_ref.id}/crossrefs")
    assert response.status_code == 200

    data = response.json()
    assert len(data) == 2

    # Every item's embedded "paper" is one of the OTHER papers, never p_ref
    other_ids = {str(p_other1.id), str(p_other2.id)}
    for item in data:
        assert item["paper"]["id"] in other_ids
        assert item["paper"]["id"] != str(p_ref.id)
        assert item["paper"]["title"] in ("Other One", "Other Two")
        assert "relation_type" in item
        assert "strength" in item

    # strong ordered before moderate
    assert data[0]["strength"] == "strong"
    assert data[0]["relation_type"] == "supports"


@pytest.mark.asyncio
async def test_get_crossrefs_filters_relation_and_min_strength(
    client, paper_factory, crossref_factory
):
    """T12: query params relation_type and min_strength filter the result set."""
    p_ref = await paper_factory(title="Ref")
    p_sup = await paper_factory(title="Sup")
    p_ext_mod = await paper_factory(title="ExtMod")
    p_ext_weak = await paper_factory(title="ExtWeak")

    await crossref_factory(
        p_ref.id, p_sup.id, relation_type="supports", strength="strong"
    )
    await crossref_factory(
        p_ref.id, p_ext_mod.id, relation_type="extends", strength="moderate"
    )
    await crossref_factory(
        p_ref.id, p_ext_weak.id, relation_type="extends", strength="weak"
    )

    # Filter by relation_type
    r = await client.get(
        f"/api/papers/{p_ref.id}/crossrefs?relation_type=extends"
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all(d["relation_type"] == "extends" for d in data)

    # Filter by min_strength=moderate -> drops the "weak" one
    r = await client.get(
        f"/api/papers/{p_ref.id}/crossrefs?relation_type=extends&min_strength=moderate"
    )
    data = r.json()
    assert len(data) == 1
    assert data[0]["strength"] == "moderate"

    # min_strength=strong -> only supports
    r = await client.get(f"/api/papers/{p_ref.id}/crossrefs?min_strength=strong")
    data = r.json()
    assert len(data) == 1
    assert data[0]["strength"] == "strong"


@pytest.mark.asyncio
async def test_get_crossrefs_unknown_paper_returns_404(client):
    fake_id = uuid.uuid4()
    response = await client.get(f"/api/papers/{fake_id}/crossrefs")
    assert response.status_code == 404
