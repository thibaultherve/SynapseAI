"""Tests for RequestIdMiddleware and SSEAwareGZipMiddleware.

RequestId is covered end-to-end against the real app (via the shared client
fixture). SSE-aware gzip is driven through a tiny ASGI harness because
httpx auto-decodes gzip responses, which would mask the bypass behavior.
"""
from __future__ import annotations

import gzip

import pytest

from app.core.middleware import RequestIdMiddleware, SSEAwareGZipMiddleware

# ---------------------------------------------------------------------------
# RequestId — integration through the live client
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_id_generated_when_absent(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    rid = response.headers.get("x-request-id")
    assert rid and len(rid) >= 16


@pytest.mark.asyncio
async def test_request_id_echoed_when_well_formed(client):
    response = await client.get(
        "/api/health", headers={"X-Request-ID": "trace-abc-123"}
    )
    assert response.headers["x-request-id"] == "trace-abc-123"


@pytest.mark.asyncio
async def test_request_id_regenerated_when_malformed(client):
    # Contains a disallowed char — middleware must reject and regenerate.
    response = await client.get(
        "/api/health", headers={"X-Request-ID": "bad id with spaces"}
    )
    rid = response.headers["x-request-id"]
    assert rid != "bad id with spaces"
    assert " " not in rid


# ---------------------------------------------------------------------------
# SSEAwareGZipMiddleware — ASGI-level tests
# ---------------------------------------------------------------------------

async def _drive(middleware, content_type: bytes, body: bytes) -> tuple[dict, bytes]:
    """Run one request through ``middleware`` and collect the sent messages."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"accept-encoding", b"gzip")],
        "query_string": b"",
        "client": ("127.0.0.1", 0),
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
        "raw_path": b"/",
        "app": None,
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    body_bytes = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in start["headers"]
    }
    return headers, body_bytes


def _app_emitting(content_type: bytes, body: bytes):
    async def app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", content_type),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        })
    return app


@pytest.mark.asyncio
async def test_sse_response_not_gzipped():
    # SSE content-type → must leave body raw so incremental flushes reach
    # the client instead of being buffered inside the gzip block window.
    body = b"data: hello\n\n" + b"data: world\n\n"
    mw = SSEAwareGZipMiddleware(
        _app_emitting(b"text/event-stream", body), minimum_size=1
    )
    headers, out = await _drive(mw, b"text/event-stream", body)
    assert "content-encoding" not in headers
    assert out == body


@pytest.mark.asyncio
async def test_json_response_is_gzipped_when_large():
    body = (b"{\"x\": 1}" * 300)  # comfortably over minimum_size
    mw = SSEAwareGZipMiddleware(
        _app_emitting(b"application/json", body), minimum_size=100
    )
    headers, out = await _drive(mw, b"application/json", body)
    assert headers.get("content-encoding") == "gzip"
    assert gzip.decompress(out) == body


@pytest.mark.asyncio
async def test_request_id_middleware_non_http_scope_passthrough():
    # Lifespan scopes must not have request-id logic applied.
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["type"])

    mw = RequestIdMiddleware(app)
    await mw({"type": "lifespan"}, None, None)
    assert calls == ["lifespan"]
