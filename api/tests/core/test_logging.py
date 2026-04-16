"""Tests for app.core.logging: JSON formatter, redaction, request-id propagation."""
from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import (
    JsonFormatter,
    RedactionFilter,
    RequestIdFilter,
    _fingerprint,
    _redact_text,
    request_id_var,
)


def _make_record(
    msg: str,
    *args,
    name: str = "app.test",
    level: int = logging.INFO,
    extra: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=args or None,
        exc_info=None,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


def _apply_filters(record: logging.LogRecord) -> None:
    assert RequestIdFilter().filter(record)
    assert RedactionFilter().filter(record)


# ---------------------------------------------------------------------------
# Redaction — unit level
# ---------------------------------------------------------------------------

def test_redact_email():
    assert _redact_text("reach me at alice@example.com asap") == (
        "reach me at <email> asap"
    )


def test_redact_long_base64():
    payload = "A" * 250
    result = _redact_text(f"token={payload}")
    assert "A" * 50 not in result
    assert "<b64:250>" in result


def test_redact_short_base64_untouched():
    # Below the 200-char floor — not treated as base64 noise.
    short = "abc123="  # not redacted (too short)
    assert _redact_text(short) == short


def test_redact_uuid_fence_delimiter():
    text = "payload <<<deadbeef1234>>> end"
    assert _redact_text(text) == "payload <delim> end"


def test_fingerprint_shape():
    fp = _fingerprint("secret pdf text")
    assert set(fp.keys()) == {"sha256", "len"}
    assert len(fp["sha256"]) == 64
    assert fp["len"] == len(b"secret pdf text")


# ---------------------------------------------------------------------------
# RedactionFilter wires redaction into records
# ---------------------------------------------------------------------------

def test_redaction_filter_rewrites_message():
    record = _make_record("contact %s now", "alice@example.com")
    _apply_filters(record)
    assert record.getMessage() == "contact <email> now"
    assert record.args is None


def test_redaction_filter_fingerprints_sensitive_extras():
    record = _make_record(
        "processing paper",
        extra={"extracted_text": "full pdf body", "paper_id": "abc-123"},
    )
    _apply_filters(record)
    assert isinstance(record.extracted_text, dict)
    assert "sha256" in record.extracted_text
    assert record.paper_id == "abc-123"  # non-sensitive extra preserved


# ---------------------------------------------------------------------------
# JsonFormatter shape + extras propagation
# ---------------------------------------------------------------------------

def test_json_formatter_emits_core_fields():
    record = _make_record("event happened")
    _apply_filters(record)
    output = JsonFormatter().format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "app.test"
    assert parsed["msg"] == "event happened"
    assert parsed["request_id"] == "-"
    assert "ts" in parsed and parsed["ts"].endswith("Z")
    assert "line" in parsed and "func" in parsed


def test_json_formatter_includes_extras():
    record = _make_record(
        "crossref_completed",
        extra={"paper_id": "abc", "pairs_kept": 5},
    )
    _apply_filters(record)
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["paper_id"] == "abc"
    assert parsed["pairs_kept"] == 5


def test_json_formatter_handles_non_serializable_extras():
    class Weird:
        def __repr__(self) -> str:
            return "<Weird>"

    record = _make_record("oops", extra={"blob": Weird()})
    _apply_filters(record)
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["blob"] == "<Weird>"


# ---------------------------------------------------------------------------
# RequestId propagation
# ---------------------------------------------------------------------------

def test_request_id_filter_copies_contextvar():
    token = request_id_var.set("rid-abc")
    try:
        record = _make_record("hello")
        _apply_filters(record)
        parsed = json.loads(JsonFormatter().format(record))
        assert parsed["request_id"] == "rid-abc"
    finally:
        request_id_var.reset(token)


def test_request_id_propagates_across_multiple_records():
    token = request_id_var.set("shared-rid")
    try:
        seen = []
        for i in range(3):
            record = _make_record("step %d", i)
            _apply_filters(record)
            seen.append(json.loads(JsonFormatter().format(record)))
    finally:
        request_id_var.reset(token)

    assert {r["request_id"] for r in seen} == {"shared-rid"}
    assert [r["msg"] for r in seen] == ["step 0", "step 1", "step 2"]


@pytest.mark.asyncio
async def test_request_id_default_is_dash_outside_request():
    # Default ContextVar value should round-trip as "-" when no middleware ran.
    record = _make_record("pre-request startup log")
    _apply_filters(record)
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["request_id"] == "-"
