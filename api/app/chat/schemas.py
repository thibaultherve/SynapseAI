import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.config import chat_settings
from app.core.schemas import AppBaseModel


class ChatMessageCreate(AppBaseModel):
    content: str = Field(..., min_length=1, max_length=chat_settings.CHAT_MAX_MESSAGE_LENGTH)
    session_id: int | None = None


class ChatMessageResponse(AppBaseModel):
    id: int
    session_id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class SessionResponse(AppBaseModel):
    id: int
    paper_id: uuid.UUID | None = None
    scope: Literal["paper", "corpus"]
    created_at: datetime
    message_count: int = 0
