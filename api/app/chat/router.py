"""Chat router: SSE streaming endpoints + session/message listing."""

import asyncio
import contextlib
import json
import logging
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import service
from app.chat.dependencies import get_session_or_404
from app.chat.exceptions import ChatBusyError, ChatCapacityError
from app.chat.models import ChatSession
from app.chat.schemas import (
    ChatMessageCreate,
    ChatMessageResponse,
    SessionResponse,
)
from app.config import chat_settings
from app.core.database import async_session, get_db
from app.core.exceptions import AppError
from app.core.schemas import ErrorResponse
from app.papers.dependencies import get_paper_or_404
from app.papers.models import Paper
from app.ratelimit import limiter

router = APIRouter(prefix="/api", tags=["chat"])

logger = logging.getLogger(__name__)

CHAT_SSE_KEEPALIVE_INTERVAL = 15.0


# Chat-only SSE capacity (separate from the processing capacity dict).
# NOTE: this dict is per-process. Under multi-worker deployments
# (uvicorn --workers N / gunicorn) capacity is tracked per worker and
# effective totals multiply by N. Move to Redis before scaling horizontally.
_chat_streams: dict[str, int] = defaultdict(int)
_CORPUS_KEY = "__corpus__"

_SSE_RESPONSES = {
    200: {
        "description": "SSE stream of chat events",
        "content": {"text/event-stream": {}},
    },
}


def _acquire_slot(key: str) -> None:
    total = sum(_chat_streams.values())
    if total >= chat_settings.CHAT_MAX_SSE_TOTAL:
        raise ChatCapacityError()
    if _chat_streams[key] >= chat_settings.CHAT_MAX_SSE_PER_PAPER:
        raise ChatBusyError()
    _chat_streams[key] += 1


def _release_slot(key: str) -> None:
    _chat_streams[key] -= 1
    if _chat_streams[key] <= 0:
        _chat_streams.pop(key, None)


def _sse_pack(event: str, data: dict, event_id: str | None = None) -> str:
    id_line = f"id: {event_id}\n" if event_id else ""
    return f"{id_line}event: {event}\ndata: {json.dumps(data)}\n\n"


async def _iter_with_keepalive(agen, request: Request):
    """Yield raw SSE frames from `agen`, emitting a keepalive ping every
    CHAT_SSE_KEEPALIVE_INTERVAL seconds of silence. Stops if the client
    disconnects. Each data event carries a fresh UUID `id:` so clients can
    track position (replay is not supported; events are not persisted)."""
    aiter = agen.__aiter__()
    try:
        while True:
            try:
                evt = await asyncio.wait_for(
                    aiter.__anext__(), timeout=CHAT_SSE_KEEPALIVE_INTERVAL
                )
            except StopAsyncIteration:
                return
            except TimeoutError:
                if await request.is_disconnected():
                    return
                yield ": ping\n\n"
                continue
            if await request.is_disconnected():
                return
            yield _sse_pack("chat", evt, event_id=uuid.uuid4().hex)
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


# ---------------------------------------------------------------------------
# POST /api/papers/:id/chat  — SSE paper-scope
# ---------------------------------------------------------------------------


@router.post(
    "/papers/{paper_id}/chat",
    status_code=200,
    description="Chat about a specific paper (SSE streaming).",
    responses={
        **_SSE_RESPONSES,
        404: {"model": ErrorResponse, "description": "Paper or session not found"},
        409: {"model": ErrorResponse, "description": "Session full or scope mismatch"},
        429: {"model": ErrorResponse, "description": "Too many chat streams for this paper"},
        503: {"model": ErrorResponse, "description": "Server at chat SSE capacity"},
    },
)
@limiter.limit(chat_settings.CHAT_RATE_LIMIT)
async def chat_paper(
    request: Request,
    body: ChatMessageCreate,
    paper: Paper = Depends(get_paper_or_404),
):
    key = str(paper.id)
    _acquire_slot(key)

    paper_id = paper.id
    user_message = body.content
    session_id = body.session_id
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        logger.info(
            "chat_sse_last_event_id_received",
            extra={"paper_id": str(paper_id), "last_event_id": last_event_id[:64]},
        )

    async def event_gen():
        try:
            async with async_session() as stream_db:
                stream_paper = await stream_db.get(Paper, paper_id)
                if stream_paper is None:
                    yield _sse_pack(
                        "chat",
                        {"type": "error", "message": "Paper disappeared mid-stream"},
                        event_id=uuid.uuid4().hex,
                    )
                    return

                try:
                    async for frame in _iter_with_keepalive(
                        service.chat_with_paper(
                            stream_db, stream_paper, user_message, session_id
                        ),
                        request,
                    ):
                        yield frame
                except AppError as exc:
                    yield _sse_pack(
                        "chat",
                        {"type": "error", "code": exc.code, "message": exc.message},
                        event_id=uuid.uuid4().hex,
                    )
        finally:
            _release_slot(key)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# POST /api/chat/corpus  — SSE corpus-scope
# ---------------------------------------------------------------------------


@router.post(
    "/chat/corpus",
    status_code=200,
    description="Chat across the entire corpus (SSE streaming).",
    responses={
        **_SSE_RESPONSES,
        404: {"model": ErrorResponse, "description": "Session not found"},
        409: {"model": ErrorResponse, "description": "Session full or scope mismatch"},
        503: {"model": ErrorResponse, "description": "Server at chat SSE capacity"},
    },
)
@limiter.limit(chat_settings.CHAT_RATE_LIMIT)
async def chat_corpus(
    request: Request,
    body: ChatMessageCreate,
):
    _acquire_slot(_CORPUS_KEY)

    user_message = body.content
    session_id = body.session_id
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        logger.info(
            "chat_sse_last_event_id_received",
            extra={"scope": "corpus", "last_event_id": last_event_id[:64]},
        )

    async def event_gen():
        try:
            async with async_session() as stream_db:
                try:
                    async for frame in _iter_with_keepalive(
                        service.chat_with_corpus(stream_db, user_message, session_id),
                        request,
                    ):
                        yield frame
                except AppError as exc:
                    yield _sse_pack(
                        "chat",
                        {"type": "error", "code": exc.code, "message": exc.message},
                        event_id=uuid.uuid4().hex,
                    )
        finally:
            _release_slot(_CORPUS_KEY)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# GET /api/papers/:id/chat/sessions
# ---------------------------------------------------------------------------


@router.get(
    "/papers/{paper_id}/chat/sessions",
    response_model=list[SessionResponse],
    status_code=200,
    description="List chat sessions for a paper (most recent first).",
    responses={404: {"model": ErrorResponse, "description": "Paper not found"}},
)
@limiter.limit("60/minute")
async def list_paper_sessions(
    request: Request,
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
):
    pairs = await service.list_sessions_for_paper(db, paper.id)
    return [
        SessionResponse(
            id=session.id,
            paper_id=session.paper_id,
            scope=session.scope,
            created_at=session.created_at,
            message_count=count,
        )
        for session, count in pairs
    ]


# ---------------------------------------------------------------------------
# GET /api/chat/sessions/:id/messages  (paginated)
# ---------------------------------------------------------------------------


@router.get(
    "/chat/sessions/{session_id}/messages",
    response_model=list[ChatMessageResponse],
    status_code=200,
    description="List chat messages for a session (paginated, oldest first).",
    responses={404: {"model": ErrorResponse, "description": "Session not found"}},
)
@limiter.limit("60/minute")
async def list_session_messages(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: ChatSession = Depends(get_session_or_404),
    db: AsyncSession = Depends(get_db),
):
    messages = await service.list_messages_paginated(
        db, session.id, limit=limit, offset=offset
    )
    return messages
