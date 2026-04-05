import uuid

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.papers.models import Paper, PaperTag
from app.tags.exceptions import DuplicateTagError, TagNotFoundError
from app.tags.models import Tag
from app.tags.schemas import TagCreate, TagUpdate


async def get_all_tags(
    db: AsyncSession, *, category: str | None = None
) -> dict[str, list[Tag]]:
    """Return all tags grouped by category."""
    query = select(Tag).order_by(Tag.category, Tag.name)
    if category:
        query = query.where(Tag.category == category)

    result = await db.execute(query)
    tags = list(result.scalars().all())

    grouped: dict[str, list[Tag]] = {}
    for tag in tags:
        grouped.setdefault(tag.category, []).append(tag)
    return grouped


async def get_tag_papers(
    db: AsyncSession, tag_id: int
) -> list[Paper]:
    """Return papers associated with a tag."""
    result = await db.execute(
        select(Paper)
        .join(PaperTag, Paper.id == PaperTag.paper_id)
        .where(PaperTag.tag_id == tag_id)
        .order_by(Paper.created_at.desc())
    )
    return list(result.scalars().all())


async def create_tag(db: AsyncSession, data: TagCreate) -> Tag:
    """Create a new tag. Raises DuplicateTagError on (name, category) conflict.

    TODO: Wire up to a POST /api/tags endpoint for manual admin tag creation.
    """
    existing = await db.execute(
        select(Tag).where(
            func.lower(Tag.name) == func.lower(data.name),
            Tag.category == data.category,
        )
    )
    if existing.scalar_one_or_none():
        raise DuplicateTagError()

    tag = Tag(name=data.name, category=data.category, description=data.description)
    db.add(tag)
    await db.flush()
    await db.refresh(tag)
    return tag


async def rename_tag(db: AsyncSession, tag: Tag, data: TagUpdate) -> Tag:
    """Rename a tag. Raises DuplicateTagError if the new name conflicts."""
    if data.name is not None:
        existing = await db.execute(
            select(Tag).where(
                func.lower(Tag.name) == func.lower(data.name),
                Tag.category == tag.category,
                Tag.id != tag.id,
            )
        )
        if existing.scalar_one_or_none():
            raise DuplicateTagError()
        tag.name = data.name
    return tag


async def delete_tag(db: AsyncSession, tag: Tag) -> None:
    """Delete a tag (CASCADE removes paper_tag rows)."""
    await db.delete(tag)


async def merge_tags(
    db: AsyncSession, source_id: int, target_id: int
) -> Tag:
    """Merge source tag into target: move associations, delete source.

    Uses FOR UPDATE to prevent concurrent modification and
    ON CONFLICT DO NOTHING for duplicate paper_tag rows.
    """
    # Lock both tags
    result = await db.execute(
        select(Tag)
        .where(Tag.id.in_([source_id, target_id]))
        .with_for_update()
    )
    tags = {t.id: t for t in result.scalars().all()}

    if source_id not in tags:
        raise TagNotFoundError(source_id)
    if target_id not in tags:
        raise TagNotFoundError(target_id)

    target = tags[target_id]

    # Move paper_tag associations (skip duplicates)
    await db.execute(
        text(
            "INSERT INTO paper_tag (paper_id, tag_id) "
            "SELECT paper_id, :target_id FROM paper_tag WHERE tag_id = :source_id "
            "ON CONFLICT (paper_id, tag_id) DO NOTHING"
        ),
        {"source_id": source_id, "target_id": target_id},
    )

    # Delete source tag (CASCADE remaining paper_tags)
    await db.execute(delete(Tag).where(Tag.id == source_id))
    await db.refresh(target)
    return target


async def resolve_tags(
    db: AsyncSession,
    tag_entries: list[dict],
) -> list[Tag]:
    """Resolve Claude's tagging output into Tag objects.

    Each entry is either:
      {"existing_id": int}  — reference to an existing tag
      {"new": {"name": str, "category": str, "description": str | None}}

    Batch-loads existing tags to avoid N+1 queries.
    Creates new tags for entries that don't match existing ones.
    Returns the list of resolved Tag objects.
    """
    # Collect IDs and new-tag keys for batch lookup
    existing_ids = [
        e["existing_id"] for e in tag_entries
        if "existing_id" in e and isinstance(e["existing_id"], int)
    ]
    new_entries = [e["new"] for e in tag_entries if "new" in e and isinstance(e["new"], dict)]

    # Batch-load existing tags by ID (single query)
    tags_by_id: dict[int, Tag] = {}
    if existing_ids:
        result = await db.execute(
            select(Tag).where(Tag.id.in_(existing_ids))
        )
        tags_by_id = {t.id: t for t in result.scalars().all()}

    # Batch-load all tags indexed by (lower_name, category) for matching (single query)
    tags_by_key: dict[tuple[str, str], Tag] = {}
    if new_entries:
        result = await db.execute(select(Tag))
        for t in result.scalars().all():
            tags_by_key[(t.name.lower(), t.category)] = t

    # Resolve each entry
    resolved: list[Tag] = []
    for entry in tag_entries:
        if "existing_id" in entry:
            tag = tags_by_id.get(entry["existing_id"])
            if not tag:
                continue
            resolved.append(tag)

        elif "new" in entry and isinstance(entry["new"], dict):
            new_data = entry["new"]
            name = new_data.get("name", "").strip()
            category = new_data.get("category", "")
            if not name or not category:
                continue

            key = (name.lower(), category)
            tag = tags_by_key.get(key)
            if not tag:
                tag = Tag(
                    name=name,
                    category=category,
                    description=new_data.get("description"),
                )
                db.add(tag)
                await db.flush()
                await db.refresh(tag)
                tags_by_key[key] = tag
            resolved.append(tag)

    return resolved


async def link_tags_to_paper(
    db: AsyncSession,
    paper_id: uuid.UUID,
    tags: list[Tag],
) -> None:
    """Link resolved Tag objects to a paper via paper_tag.

    Uses a single multi-row INSERT with ON CONFLICT DO NOTHING.
    """
    if not tags:
        return

    # Build a single multi-row INSERT to avoid N queries
    values_clause = ", ".join(
        f"(:paper_id, :tag_id_{i})" for i in range(len(tags))
    )
    params: dict = {"paper_id": paper_id}
    for i, tag in enumerate(tags):
        params[f"tag_id_{i}"] = tag.id

    await db.execute(
        text(
            f"INSERT INTO paper_tag (paper_id, tag_id) "
            f"VALUES {values_clause} "
            f"ON CONFLICT (paper_id, tag_id) DO NOTHING"
        ),
        params,
    )
    await db.flush()
