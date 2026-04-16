"""ASGI middlewares for request correlation and SSE-safe compression."""
from __future__ import annotations

import uuid

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

_REQUEST_ID_HEADER = b"x-request-id"
_MAX_REQUEST_ID_LEN = 128
_REQUEST_ID_ALLOWED = frozenset("-_.")


def _normalize_request_id(raw: str) -> str | None:
    """Reject obviously malformed incoming request IDs."""
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > _MAX_REQUEST_ID_LEN:
        return None
    if not all(c.isalnum() or c in _REQUEST_ID_ALLOWED for c in trimmed):
        return None
    return trimmed


class RequestIdMiddleware:
    """Assign a correlation id to every HTTP request.

    Reads ``X-Request-ID`` from the incoming headers when present and
    well-formed (alphanumeric + ``-_.`` only, <=128 chars), otherwise
    generates ``uuid4().hex``. Stores the id in a ContextVar so log records
    can pick it up, and echoes it in ``X-Request-ID`` on the response.

    Implemented as a raw ASGI middleware (not ``BaseHTTPMiddleware``) so it
    stays out of the way of SSE streaming and background tasks.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid: str | None = None
        for key, value in scope.get("headers") or ():
            if key.lower() == _REQUEST_ID_HEADER:
                rid = _normalize_request_id(value.decode("latin-1", errors="replace"))
                break
        if rid is None:
            rid = uuid.uuid4().hex

        encoded = rid.encode("latin-1")
        token = request_id_var.set(rid)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for (k, v) in (message.get("headers") or [])
                    if k.lower() != _REQUEST_ID_HEADER
                ]
                headers.append((_REQUEST_ID_HEADER, encoded))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


def _get_header_value(headers: list[tuple[bytes, bytes]] | None, key: bytes) -> bytes | None:
    if not headers:
        return None
    for k, v in headers:
        if k.lower() == key:
            return v
    return None


class _SSEAwareGZipResponder(GZipResponder):
    """GZipResponder that bypasses compression for SSE responses.

    Current Starlette already excludes ``text/event-stream`` from its
    default gzip path, but we keep an explicit check to stay correct if
    that default ever changes upstream — and to make the intent visible
    at the ASGI layer where it matters for streaming endpoints.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Keep the parent's buffer/file lifecycle — ``with`` ensures the
        # gzip file is closed even on the SSE bypass path.
        with self.gzip_buffer, self.gzip_file:
            self.send = send
            self._downstream = send
            self._is_sse = False
            await self.app(scope, receive, self._route)

    async def _route(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            content_type = _get_header_value(message.get("headers"), b"content-type")
            if content_type and content_type.lower().startswith(b"text/event-stream"):
                self._is_sse = True

        if self._is_sse:
            await self._downstream(message)
            return

        await self.send_with_compression(message)


class SSEAwareGZipMiddleware(GZipMiddleware):
    """GZip middleware that never compresses ``text/event-stream`` responses.

    Buffered compression breaks incremental SSE flushing (events are held
    until a block boundary), so we detect the content type on the first
    ``http.response.start`` and fall back to passing the raw response
    through untouched.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        accept_encoding = Headers(scope=scope).get("Accept-Encoding", "")
        if "gzip" not in accept_encoding:
            await self.app(scope, receive, send)
            return

        responder = _SSEAwareGZipResponder(
            self.app, self.minimum_size, self.compresslevel
        )
        await responder(scope, receive, send)
