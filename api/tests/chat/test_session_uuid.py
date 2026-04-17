"""Phase 5 (UUID session migration) — enumeration resistance + response shape."""

import re
import uuid

import pytest

from app.chat.models import ChatSession

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@pytest.mark.asyncio
async def test_list_messages_rejects_integer_session_id(client):
    """FastAPI must reject int path params — the path type is uuid.UUID now."""
    resp = await client.get("/api/chat/sessions/1/messages")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_messages_random_uuid_returns_404(client):
    resp = await client.get(f"/api/chat/sessions/{uuid.uuid4()}/messages")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_sessions_response_id_is_uuid_string(
    client, db, paper_factory
):
    from app.core.enums import SourceType

    paper = await paper_factory(source_type=SourceType.PDF, title="UUID test")
    session = ChatSession(paper_id=paper.id, scope="paper")
    db.add(session)
    await db.commit()
    await db.refresh(session)

    resp = await client.get(f"/api/papers/{paper.id}/chat/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert isinstance(body[0]["id"], str)
    assert _UUID_RE.match(body[0]["id"])
    assert body[0]["id"] == str(session.id)


@pytest.mark.asyncio
async def test_session_ids_are_distinct_uuids(db, paper_factory):
    """Server-generated IDs must be non-sequential UUIDs."""
    from app.core.enums import SourceType

    paper = await paper_factory(source_type=SourceType.PDF, title="distinct")
    a = ChatSession(paper_id=paper.id, scope="paper")
    b = ChatSession(paper_id=paper.id, scope="paper")
    db.add(a)
    db.add(b)
    await db.commit()
    await db.refresh(a)
    await db.refresh(b)

    assert isinstance(a.id, uuid.UUID)
    assert isinstance(b.id, uuid.UUID)
    assert a.id != b.id


@pytest.mark.asyncio
async def test_chat_message_create_rejects_int_session_id(client, paper_factory):
    """ChatMessageCreate.session_id is uuid.UUID|None — int in body is rejected."""
    from app.core.enums import SourceType

    paper = await paper_factory(source_type=SourceType.PDF, title="bad sid")
    resp = await client.post(
        f"/api/papers/{paper.id}/chat",
        json={"content": "hi", "scope": "paper", "session_id": 42},
    )
    assert resp.status_code == 422
