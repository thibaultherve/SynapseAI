import uuid

import pytest
from sqlalchemy import select

from app.papers.models import Paper, PaperTag
from app.tags.models import Tag
from app.tags.service import link_tags_to_paper, resolve_tags


# --- Fixtures ---


@pytest.fixture
async def make_tag(db):
    """Create a tag directly in the DB."""

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
async def make_paper(db):
    """Create a minimal paper for linking tests."""

    async def _create() -> Paper:
        paper = Paper(id=uuid.uuid4(), source_type="web", url="https://example.com")
        db.add(paper)
        await db.flush()
        return paper

    return _create


# ==========================================================================
# resolve_tags
# ==========================================================================


class TestResolveTags:

    @pytest.mark.asyncio
    async def test_empty_entries(self, db):
        result = await resolve_tags(db, [])
        assert result == []

    @pytest.mark.asyncio
    async def test_existing_id_found(self, db, make_tag):
        tag = await make_tag("CNN", "technique")
        result = await resolve_tags(db, [{"existing_id": tag.id}])
        assert len(result) == 1
        assert result[0].id == tag.id

    @pytest.mark.asyncio
    async def test_existing_id_not_found(self, db):
        result = await resolve_tags(db, [{"existing_id": 99999}])
        assert result == []

    @pytest.mark.asyncio
    async def test_existing_id_non_int_skipped(self, db):
        result = await resolve_tags(db, [{"existing_id": "bad"}])
        assert result == []

    @pytest.mark.asyncio
    async def test_new_tag_created(self, db):
        entries = [{"new": {"name": "scRNA-seq", "category": "technique", "description": "Single-cell RNA"}}]
        result = await resolve_tags(db, entries)

        assert len(result) == 1
        assert result[0].name == "scRNA-seq"
        assert result[0].category == "technique"
        assert result[0].id is not None  # persisted

    @pytest.mark.asyncio
    async def test_new_tag_matches_existing_case_insensitive(self, db, make_tag):
        existing = await make_tag("Deep Learning", "technique")

        entries = [{"new": {"name": "deep learning", "category": "technique"}}]
        result = await resolve_tags(db, entries)

        assert len(result) == 1
        assert result[0].id == existing.id  # reused, not duplicated

    @pytest.mark.asyncio
    async def test_new_tag_different_category_creates_separate(self, db, make_tag):
        await make_tag("Imaging", "technique")

        entries = [{"new": {"name": "Imaging", "category": "topic"}}]
        result = await resolve_tags(db, entries)

        assert len(result) == 1
        assert result[0].category == "topic"

        # Both should exist
        all_tags = (await db.execute(select(Tag))).scalars().all()
        assert len(all_tags) == 2

    @pytest.mark.asyncio
    async def test_new_tag_empty_name_skipped(self, db):
        entries = [{"new": {"name": "", "category": "technique"}}]
        result = await resolve_tags(db, entries)
        assert result == []

    @pytest.mark.asyncio
    async def test_new_tag_empty_category_skipped(self, db):
        entries = [{"new": {"name": "valid", "category": ""}}]
        result = await resolve_tags(db, entries)
        assert result == []

    @pytest.mark.asyncio
    async def test_unknown_entry_format_skipped(self, db):
        entries = [{"garbage": True}, {"other": "data"}]
        result = await resolve_tags(db, entries)
        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_entries(self, db, make_tag):
        existing = await make_tag("CNN", "technique")

        entries = [
            {"existing_id": existing.id},       # valid
            {"existing_id": 99999},              # invalid — skipped
            {"new": {"name": "fMRI", "category": "technique"}},  # new
            {"new": {"name": "", "category": "topic"}},          # invalid — skipped
            {"garbage": True},                   # ignored
        ]
        result = await resolve_tags(db, entries)

        assert len(result) == 2
        assert result[0].id == existing.id
        assert result[1].name == "fMRI"

    @pytest.mark.asyncio
    async def test_duplicate_new_entries_deduplicated(self, db):
        """Two entries for the same new tag should create it only once."""
        entries = [
            {"new": {"name": "Neuroscience", "category": "sub_domain"}},
            {"new": {"name": "neuroscience", "category": "sub_domain"}},  # same, different case
        ]
        result = await resolve_tags(db, entries)

        assert len(result) == 2
        assert result[0].id == result[1].id  # same tag object

        all_tags = (await db.execute(select(Tag))).scalars().all()
        assert len(all_tags) == 1


# ==========================================================================
# link_tags_to_paper
# ==========================================================================


class TestLinkTagsToPaper:

    @pytest.mark.asyncio
    async def test_empty_tags_noop(self, db, make_paper):
        paper = await make_paper()
        await link_tags_to_paper(db, paper.id, [])

        rows = (await db.execute(
            select(PaperTag).where(PaperTag.paper_id == paper.id)
        )).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_links_tags_to_paper(self, db, make_paper, make_tag):
        paper = await make_paper()
        t1 = await make_tag("CNN", "technique")
        t2 = await make_tag("Neuroscience", "sub_domain")

        await link_tags_to_paper(db, paper.id, [t1, t2])

        rows = (await db.execute(
            select(PaperTag).where(PaperTag.paper_id == paper.id)
        )).scalars().all()
        linked_ids = {r.tag_id for r in rows}
        assert linked_ids == {t1.id, t2.id}

    @pytest.mark.asyncio
    async def test_duplicate_link_no_error(self, db, make_paper, make_tag):
        paper = await make_paper()
        tag = await make_tag("CNN", "technique")

        # Link once
        await link_tags_to_paper(db, paper.id, [tag])
        # Link again — should not raise
        await link_tags_to_paper(db, paper.id, [tag])

        rows = (await db.execute(
            select(PaperTag).where(PaperTag.paper_id == paper.id)
        )).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_partial_duplicate_links(self, db, make_paper, make_tag):
        """Some tags already linked, some new — all should be present after."""
        paper = await make_paper()
        t1 = await make_tag("CNN", "technique")
        t2 = await make_tag("RNN", "technique")

        await link_tags_to_paper(db, paper.id, [t1])  # link t1 first
        await link_tags_to_paper(db, paper.id, [t1, t2])  # t1 duplicate, t2 new

        rows = (await db.execute(
            select(PaperTag).where(PaperTag.paper_id == paper.id)
        )).scalars().all()
        assert len(rows) == 2
