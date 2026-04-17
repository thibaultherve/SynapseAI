import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.exceptions import SessionNotFoundError
from app.chat.models import ChatSession
from app.core.database import get_db


async def get_session_or_404(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ChatSession:
    # TODO(auth): once user authentication lands, assert the session
    # belongs to the caller (or to a paper they can access). UUIDs are
    # non-enumerable but still leak access when known.
    session = await db.get(ChatSession, session_id)
    if not session:
        raise SessionNotFoundError(session_id)
    return session
