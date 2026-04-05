from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.tags.exceptions import TagNotFoundError
from app.tags.models import Tag


async def get_tag_or_404(
    tag_id: int,
    db: AsyncSession = Depends(get_db),
) -> Tag:
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise TagNotFoundError(tag_id)
    return tag
