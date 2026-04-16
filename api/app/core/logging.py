"""Central logging configuration for SynapseAI.

Emits single-line JSON to stdout with a fixed envelope:

    {
        "ts": "2026-04-17T12:34:56.789Z",
        "level": "INFO",
        "logger": "app.processing.service",
        "msg": "crossref_completed",
        "request_id": "<hex>",
        "module": "service",
        "func": "run_crossref_step",
        "line": 42,
        ...extras
    }

Two filters run on every record before the formatter:

- ``RequestIdFilter`` copies the current ``request_id_var`` ContextVar value
  onto the record so each log line carries the correlation id.
- ``RedactionFilter`` strips accidentally leaked sensitive data:
    * emails  -> ``<email>``
    * long base64 runs (>=200 chars) -> ``<b64:N>`` (N = length)
    * UUID fence delimiters ``<<<deadbeef>>>`` -> ``<delim>``
    * named extras (``extracted_text``, ``user_message``, ``prompt``,
      ``summary``) -> ``{"sha256": ..., "len": ...}``

Redaction is best-effort — it protects against accidental leakage into
support/ops eyes, not against a malicious internal operator who can read
stdout directly.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import sys
import time
from typing import Any

# ContextVar shared with app.core.middleware.RequestIdMiddleware.
# Default "-" so logs emitted outside a request (startup, background tasks
# that never crossed the middleware) stay parseable.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_B64_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_DELIM_RE = re.compile(r"<<<[A-Za-z0-9._-]{1,64}>>>")

_SENSITIVE_EXTRA_KEYS: frozenset[str] = frozenset(
    {"extracted_text", "user_message", "prompt", "summary"}
)

# LogRecord attributes set by the stdlib — anything else in ``record.__dict__``
# is treated as a user-supplied extra.
_STANDARD_RECORD_KEYS: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName", "asctime",
    }
)


def _redact_text(text: str) -> str:
    text = _EMAIL_RE.sub("<email>", text)
    text = _B64_RE.sub(lambda m: f"<b64:{len(m.group(0))}>", text)
    text = _DELIM_RE.sub("<delim>", text)
    return text


def _fingerprint(value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8", errors="replace")
    else:
        raw = str(value).encode("utf-8", errors="replace")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "len": len(raw)}


class RedactionFilter(logging.Filter):
    """Rewrite the record's message + sensitive extras in-place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            formatted = record.getMessage()
        except Exception:
            formatted = str(record.msg)
        record.msg = _redact_text(formatted)
        record.args = None

        for key in _SENSITIVE_EXTRA_KEYS:
            if hasattr(record, key):
                setattr(record, key, _fingerprint(getattr(record, key)))
        return True


class RequestIdFilter(logging.Filter):
    """Stamp the record with the current request_id ContextVar value."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
        ) + f".{int(record.msecs):03d}Z"

        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS or key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = record.stack_info

        return json.dumps(payload, ensure_ascii=False, default=str)


_configured: bool = False


def configure_logging(level: str | None = None) -> None:
    """Install the JSON handler + filters on the root logger.

    Idempotent by design: calling this multiple times (from tests, from
    module imports) only installs one handler. Clearing existing handlers
    lets us reclaim the logger from uvicorn's default setup so every record
    funnels through the same JSON pipeline.
    """
    global _configured
    if _configured:
        return

    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True

    _configured = True


def _reset_for_tests() -> None:
    """Drop the install-once guard — used only by the logging test module."""
    global _configured
    _configured = False
