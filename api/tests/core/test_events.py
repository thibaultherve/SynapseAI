"""Tests for app.core.events: subscribe/publish semantics, isolation, secrecy."""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.core import events
from app.core.events import Event, publish, subscribe


@pytest.fixture(autouse=True)
def _reset_subs():
    """Keep each test's subscriber list isolated from its neighbors."""
    events._subs.clear()
    yield
    events._subs.clear()


async def test_subscribe_publish_happy_path():
    seen: list[dict] = []

    async def handler(**payload):
        seen.append(payload)

    subscribe(Event.PAPER_PROCESSED, handler)
    await publish(Event.PAPER_PROCESSED, paper_id="abc-123")

    assert seen == [{"paper_id": "abc-123"}]


async def test_handler_exception_does_not_block_others(caplog):
    order: list[str] = []

    async def boom(**_payload):
        order.append("boom")
        raise RuntimeError("handler exploded")

    async def ok(**_payload):
        order.append("ok")

    subscribe(Event.PAPER_PROCESSED, boom)
    subscribe(Event.PAPER_PROCESSED, ok)

    with caplog.at_level(logging.ERROR, logger="app.core.events"):
        await publish(Event.PAPER_PROCESSED, paper_id="abc-123")

    # Second handler ran despite first one raising.
    assert order == ["boom", "ok"]

    failure_records = [r for r in caplog.records if r.msg == "event_handler_failed"]
    assert len(failure_records) == 1
    assert failure_records[0].event == Event.PAPER_PROCESSED.value
    assert failure_records[0].handler.endswith("boom")


async def test_handler_timeout_is_logged(caplog, monkeypatch):
    """A handler that exceeds HANDLER_TIMEOUT_S is cancelled and logged."""
    monkeypatch.setattr(events, "HANDLER_TIMEOUT_S", 0.05)

    async def sleeper(**_payload):
        await asyncio.sleep(1.0)

    subscribe(Event.PAPER_PROCESSED, sleeper)

    with caplog.at_level(logging.ERROR, logger="app.core.events"):
        await publish(Event.PAPER_PROCESSED, paper_id="abc-123")

    failure_records = [r for r in caplog.records if r.msg == "event_handler_failed"]
    assert len(failure_records) == 1
    assert failure_records[0].handler.endswith("sleeper")
    # The underlying cause is TimeoutError (raised by asyncio.wait_for).
    assert failure_records[0].exc_info is not None
    exc_type = failure_records[0].exc_info[0]
    assert issubclass(exc_type, TimeoutError)


async def test_duplicate_subscription_warns_and_deduplicates(caplog):
    calls = 0

    async def handler(**_payload):
        nonlocal calls
        calls += 1

    subscribe(Event.PAPER_PROCESSED, handler)

    with caplog.at_level(logging.WARNING, logger="app.core.events"):
        subscribe(Event.PAPER_PROCESSED, handler)

    # Only one registration survives.
    assert events._subs[Event.PAPER_PROCESSED] == [handler]

    warn_records = [r for r in caplog.records if r.msg == "event_handler_duplicate_subscription"]
    assert len(warn_records) == 1

    await publish(Event.PAPER_PROCESSED, paper_id="abc-123")
    assert calls == 1


async def test_payload_never_appears_in_failure_log_extras(caplog):
    """Payload contents must stay out of the observability pipeline.

    Regression guard: even when a handler raises, neither the keys nor
    the values of the payload end up as attributes on the log record.
    """
    secret_payload = {
        "paper_id": "uuid-should-not-leak",
        "session_token": "super-secret-token-value",
    }

    async def boom(**_payload):
        raise RuntimeError("nope")

    subscribe(Event.PAPER_PROCESSED, boom)

    with caplog.at_level(logging.ERROR, logger="app.core.events"):
        await publish(Event.PAPER_PROCESSED, **secret_payload)

    failure_records = [r for r in caplog.records if r.msg == "event_handler_failed"]
    assert len(failure_records) == 1
    record = failure_records[0]

    # Neither payload keys nor values are attached to the record.
    for key, value in secret_payload.items():
        assert not hasattr(record, key), f"payload key '{key}' leaked into log record"
        formatted = record.getMessage()
        assert value not in formatted
        for attr_name in vars(record):
            assert value != getattr(record, attr_name), (
                f"payload value leaked via record attr {attr_name!r}"
            )
