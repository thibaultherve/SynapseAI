"""Chat service: session management, RAG context building, chat orchestration."""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.constants import CHAT_PROMPT
from app.chat.exceptions import (
    SessionFullError,
    SessionNotFoundError,
    SessionScopeMismatchError,
)
from app.chat.models import ChatMessage, ChatSession
from app.config import chat_settings
from app.core.embedding_client import encode_text
from app.core.llm_client import build_fenced_prompt, stream_claude
from app.papers.models import Paper
from app.processing.models import PaperEmbedding

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


async def get_or_create_session(
    db: AsyncSession,
    *,
    scope: str,
    paper_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
) -> ChatSession:
    """Return an existing session (validating scope/paper match) or create one."""
    if session_id is not None:
        session = await db.get(ChatSession, session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.scope != scope or session.paper_id != paper_id:
            raise SessionScopeMismatchError()
        return session

    session = ChatSession(scope=scope, paper_id=paper_id)
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def count_session_messages(db: AsyncSession, session_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)
    )
    return int(result.scalar() or 0)


async def get_recent_history(
    db: AsyncSession, session_id: uuid.UUID, limit: int
) -> list[ChatMessage]:
    """Return the last ``limit`` messages for a session, oldest-first."""
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(desc(ChatMessage.id))
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()
    return rows


async def list_sessions_for_paper(
    db: AsyncSession, paper_id: uuid.UUID
) -> list[tuple[ChatSession, int]]:
    """Return sessions for a paper with their message counts."""
    result = await db.execute(
        select(ChatSession, func.count(ChatMessage.id))
        .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.paper_id == paper_id, ChatSession.scope == "paper")
        .group_by(ChatSession.id)
        .order_by(desc(ChatSession.created_at))
    )
    return [(session, int(count or 0)) for session, count in result.all()]


async def list_messages_paginated(
    db: AsyncSession, session_id: uuid.UUID, limit: int, offset: int
) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------


async def _top_k_chunks(
    db: AsyncSession,
    query: str,
    limit: int,
    paper_id: uuid.UUID | None = None,
) -> list[tuple[uuid.UUID, str | None]]:
    """Return top-K (paper_id, chunk_text) by cosine similarity.

    Filters by paper_id when provided.
    """
    query_vector = await encode_text(query)
    stmt = select(
        PaperEmbedding.paper_id,
        PaperEmbedding.chunk_text,
    ).order_by(PaperEmbedding.embedding.cosine_distance(query_vector)).limit(limit)

    if paper_id is not None:
        stmt = stmt.where(PaperEmbedding.paper_id == paper_id)

    result = await db.execute(stmt)
    return [(row.paper_id, row.chunk_text) for row in result.all()]


async def build_paper_context(
    db: AsyncSession, paper: Paper, query: str
) -> str:
    """Build RAG context for paper-scope chat.

    Layout: short_summary + key_findings + top-K chunks (capped per config).
    """
    parts: list[str] = []
    title = paper.title or "Untitled"
    parts.append(f"[Paper: {title}]")

    if paper.short_summary:
        parts.append(f"<short_summary>\n{paper.short_summary}\n</short_summary>")
    if paper.key_findings:
        parts.append(f"<key_findings>\n{paper.key_findings}\n</key_findings>")

    chunks = await _top_k_chunks(
        db, query, chat_settings.CHAT_MAX_CONTEXT_CHUNKS, paper_id=paper.id
    )
    for idx, (_, text) in enumerate(chunks, start=1):
        if text:
            parts.append(f"<chunk index=\"{idx}\">\n{text}\n</chunk>")

    return await asyncio.to_thread(
        _truncate_to_budget,
        "\n\n".join(parts),
        chat_settings.CHAT_MAX_CONTEXT_TOKENS,
    )


async def build_corpus_context(db: AsyncSession, query: str) -> str:
    """Build RAG context for corpus-scope chat via semantic search across all papers."""
    chunks = await _top_k_chunks(
        db, query, chat_settings.CHAT_MAX_CONTEXT_CHUNKS, paper_id=None
    )
    if not chunks:
        return "(No relevant chunks found in the corpus.)"

    paper_ids = list({pid for pid, _ in chunks})
    titles = await _get_paper_titles(db, paper_ids)

    parts: list[str] = []
    for idx, (pid, text) in enumerate(chunks, start=1):
        if not text:
            continue
        title = titles.get(pid, "Untitled")
        parts.append(
            f"<chunk index=\"{idx}\" paper_id=\"{pid}\" title=\"{title}\">\n{text}\n</chunk>"
        )

    return await asyncio.to_thread(
        _truncate_to_budget,
        "\n\n".join(parts),
        chat_settings.CHAT_MAX_CONTEXT_TOKENS,
    )


async def _get_paper_titles(
    db: AsyncSession, paper_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not paper_ids:
        return {}
    result = await db.execute(
        select(Paper.id, Paper.title).where(Paper.id.in_(paper_ids))
    )
    return {pid: (title or "Untitled") for pid, title in result.all()}


def _truncate_to_budget(text: str, token_budget: int) -> str:
    """Truncate text to approximately ``token_budget`` tokens (~4 chars/token)."""
    char_budget = max(0, token_budget) * 4
    if len(text) <= char_budget:
        return text
    return text[:char_budget] + "\n\n[...truncated for context budget...]"


def _format_history(messages: list[ChatMessage]) -> str:
    if not messages:
        return "(no prior messages)"
    lines: list[str] = []
    for msg in messages:
        lines.append(f"[{msg.role}] {msg.content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chat orchestration
# ---------------------------------------------------------------------------


async def chat_with_paper(
    db: AsyncSession,
    paper: Paper,
    user_message: str,
    session_id: uuid.UUID | None,
) -> AsyncGenerator[dict, None]:
    """Orchestrate a paper-scope chat turn. Yields SSE-ready event dicts."""
    async for event in _chat_stream(
        db=db,
        scope="paper",
        paper_id=paper.id,
        paper=paper,
        user_message=user_message,
        session_id=session_id,
    ):
        yield event


async def chat_with_corpus(
    db: AsyncSession,
    user_message: str,
    session_id: uuid.UUID | None,
) -> AsyncGenerator[dict, None]:
    """Orchestrate a corpus-scope chat turn."""
    async for event in _chat_stream(
        db=db,
        scope="corpus",
        paper_id=None,
        paper=None,
        user_message=user_message,
        session_id=session_id,
    ):
        yield event


async def _chat_stream(
    *,
    db: AsyncSession,
    scope: str,
    paper_id: uuid.UUID | None,
    paper: Paper | None,
    user_message: str,
    session_id: uuid.UUID | None,
) -> AsyncGenerator[dict, None]:
    session = await get_or_create_session(
        db, scope=scope, paper_id=paper_id, session_id=session_id
    )

    count = await count_session_messages(db, session.id)
    if count >= chat_settings.CHAT_MAX_MESSAGES_PER_SESSION:
        raise SessionFullError(chat_settings.CHAT_MAX_MESSAGES_PER_SESSION)

    history = await get_recent_history(
        db, session.id, chat_settings.CHAT_MAX_HISTORY_MESSAGES
    )

    if scope == "paper":
        assert paper is not None
        context = await build_paper_context(db, paper, user_message)
    else:
        context = await build_corpus_context(db, user_message)

    prompt = build_fenced_prompt(
        CHAT_PROMPT,
        user_blocks={
            "retrieved_context": context,
            "conversation_history": _format_history(history),
            "user_question": user_message,
        },
    )

    # Persist the user message eagerly so it is visible even if streaming fails.
    user_row = ChatMessage(session_id=session.id, role="user", content=user_message)
    db.add(user_row)
    await db.flush()
    await db.refresh(user_row)
    await db.commit()

    # Session metadata event for the client. UUID serialized as string for
    # JSON compatibility in the SSE envelope.
    yield {"type": "session", "session_id": str(session.id)}

    full_text_parts: list[str] = []
    errored = False
    async for chunk in stream_claude(
        prompt, timeout_per_chunk=chat_settings.CHAT_CLAUDE_TIMEOUT_PER_CHUNK
    ):
        if chunk.get("type") == "content":
            text = chunk.get("text", "")
            full_text_parts.append(text)
            yield chunk
        elif chunk.get("type") == "error":
            errored = True
            yield chunk
        elif chunk.get("type") == "done":
            yield chunk

    if not errored:
        assistant_content = "".join(full_text_parts)
        if assistant_content:
            assistant_row = ChatMessage(
                session_id=session.id,
                role="assistant",
                content=assistant_content,
            )
            db.add(assistant_row)
            await db.commit()
