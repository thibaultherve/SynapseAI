"""T29-T33, T35: Chat router tests — SSE streaming, sessions, pagination, limits, validation."""

import contextlib
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.chat import router as chat_router_module
from app.chat.models import ChatMessage, ChatSession
from app.core.enums import SourceType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_stream_claude(monkeypatch):
    """Patch asyncio.create_subprocess_exec for stream_claude.

    The fake subprocess streams two stream-json lines containing text content,
    then closes stdout and returns rc=0.
    """
    stdout_lines = [
        json.dumps({
            "type": "content_block_delta",
            "delta": {"text": "Hello "},
        }).encode() + b"\n",
        json.dumps({
            "type": "content_block_delta",
            "delta": {"text": "world."},
        }).encode() + b"\n",
        b"",  # EOF
    ]
    idx = {"i": 0}

    async def fake_readline():
        i = idx["i"]
        if i >= len(stdout_lines):
            return b""
        idx["i"] += 1
        return stdout_lines[i]

    mock_stdout = MagicMock()
    mock_stdout.readline = fake_readline

    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_stdin.close = MagicMock()

    mock_stderr = MagicMock()
    mock_stderr.read = AsyncMock(return_value=b"")

    mock_process = MagicMock()
    mock_process.stdout = mock_stdout
    mock_process.stdin = mock_stdin
    mock_process.stderr = mock_stderr
    mock_process.wait = AsyncMock(return_value=0)
    mock_process.returncode = 0
    mock_process.kill = MagicMock()

    async def fake_create(*args, **kwargs):
        return mock_process

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    return mock_process


@pytest.fixture(autouse=True)
def _reset_chat_streams():
    """Clear chat SSE capacity dict between tests."""
    chat_router_module._chat_streams.clear()
    yield
    chat_router_module._chat_streams.clear()


async def _read_sse(response) -> list[dict]:
    """Collect SSE data lines into parsed dicts."""
    events: list[dict] = []
    async for line in response.aiter_lines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(payload))
    return events


# ---------------------------------------------------------------------------
# T29: POST /api/papers/:id/chat — SSE streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_paper_streams_assistant_text(
    client, paper_factory, embedding_factory, mock_embedding, mock_stream_claude
):
    paper = await paper_factory(
        source_type=SourceType.PDF,
        title="Test paper",
        short_summary="Summary",
        key_findings="Findings",
    )
    await embedding_factory(paper.id, chunks=["chunk content"])

    async with client.stream(
        "POST",
        f"/api/papers/{paper.id}/chat",
        json={"content": "What is this about?", "scope": "paper"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = await _read_sse(response)

    types = [e.get("type") for e in events]
    assert "session" in types
    assert "content" in types
    # full assistant text assembled from deltas
    contents = [e["text"] for e in events if e.get("type") == "content"]
    assert "".join(contents) == "Hello world."


# ---------------------------------------------------------------------------
# T30: POST /api/chat/corpus — SSE streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_corpus_streams_assistant_text(
    client, paper_factory, embedding_factory, mock_embedding, mock_stream_claude
):
    paper = await paper_factory(source_type=SourceType.PDF, title="Any paper")
    await embedding_factory(paper.id, chunks=["corpus chunk"])

    async with client.stream(
        "POST",
        "/api/chat/corpus",
        json={"content": "Cross-paper question", "scope": "corpus"},
    ) as response:
        assert response.status_code == 200
        events = await _read_sse(response)

    types = [e.get("type") for e in events]
    assert "session" in types
    assert "content" in types


# ---------------------------------------------------------------------------
# T31: Chat session creation + message persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_persists_user_and_assistant_messages(
    client, db, paper_factory, embedding_factory, mock_embedding, mock_stream_claude
):
    paper = await paper_factory(source_type=SourceType.PDF, title="Persist test")
    await embedding_factory(paper.id, chunks=["c"])

    async with client.stream(
        "POST",
        f"/api/papers/{paper.id}/chat",
        json={"content": "Hi there", "scope": "paper"},
    ) as response:
        assert response.status_code == 200
        events = await _read_sse(response)

    session_event = next(e for e in events if e.get("type") == "session")
    session_id = session_event["session_id"]

    # A new session was created, and both user + assistant messages persisted.
    from sqlalchemy import select

    session = await db.get(ChatSession, session_id)
    assert session is not None
    assert session.paper_id == paper.id
    assert session.scope == "paper"

    msgs = (await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
    )).scalars().all()

    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "Hi there"
    assert msgs[1].content == "Hello world."


# ---------------------------------------------------------------------------
# T32: GET sessions + GET messages (pagination)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_and_messages_pagination(
    client, db, paper_factory
):
    paper = await paper_factory(source_type=SourceType.PDF, title="List test")

    session = ChatSession(paper_id=paper.id, scope="paper")
    db.add(session)
    await db.flush()

    for i in range(5):
        db.add(ChatMessage(
            session_id=session.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"msg-{i}",
        ))
    await db.commit()

    resp = await client.get(f"/api/papers/{paper.id}/chat/sessions")
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == session.id
    assert sessions[0]["message_count"] == 5

    resp = await client.get(
        f"/api/chat/sessions/{session.id}/messages",
        params={"limit": 2, "offset": 1},
    )
    assert resp.status_code == 200
    page = resp.json()
    assert len(page) == 2
    assert page[0]["content"] == "msg-1"
    assert page[1]["content"] == "msg-2"


# ---------------------------------------------------------------------------
# T33: Chat SSE limits (capacity, busy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_busy_when_paper_slot_taken(
    client, paper_factory, embedding_factory, mock_embedding
):
    paper = await paper_factory(source_type=SourceType.PDF, title="Busy")
    await embedding_factory(paper.id, chunks=["c"])

    chat_router_module._chat_streams[str(paper.id)] = (
        chat_router_module.chat_settings.CHAT_MAX_SSE_PER_PAPER
    )

    resp = await client.post(
        f"/api/papers/{paper.id}/chat",
        json={"content": "hi", "scope": "paper"},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "CHAT_BUSY"


@pytest.mark.asyncio
async def test_chat_capacity_when_total_full(
    client, paper_factory, embedding_factory, mock_embedding
):
    paper = await paper_factory(source_type=SourceType.PDF, title="Cap")
    await embedding_factory(paper.id, chunks=["c"])

    chat_router_module._chat_streams["__corpus__"] = (
        chat_router_module.chat_settings.CHAT_MAX_SSE_TOTAL
    )

    resp = await client.post(
        f"/api/papers/{paper.id}/chat",
        json={"content": "hi", "scope": "paper"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "CHAT_CAPACITY"


# ---------------------------------------------------------------------------
# T35: Chat message validation (empty, too long, unknown paper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_empty_content_rejected(client, paper_factory):
    paper = await paper_factory(source_type=SourceType.PDF, title="v")
    resp = await client.post(
        f"/api/papers/{paper.id}/chat",
        json={"content": "", "scope": "paper"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_too_long_content_rejected(client, paper_factory):
    from app.config import chat_settings

    paper = await paper_factory(source_type=SourceType.PDF, title="v")
    resp = await client.post(
        f"/api/papers/{paper.id}/chat",
        json={
            "content": "x" * (chat_settings.CHAT_MAX_MESSAGE_LENGTH + 1),
            "scope": "paper",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_unknown_paper_404(client):
    resp = await client.post(
        f"/api/papers/{uuid.uuid4()}/chat",
        json={"content": "hi", "scope": "paper"},
    )
    assert resp.status_code == 404
