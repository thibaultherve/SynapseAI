"""Tests T29-T30: InsightDebouncer (debounce timing, lock serialization)."""

import asyncio

import pytest

from app.insights.debouncer import InsightDebouncer


@pytest.mark.asyncio
async def test_debouncer_coalesces_rapid_schedules(monkeypatch):
    """T29: 3 rapid schedule() calls within debounce window -> 1 generation."""
    generation_count = {"n": 0}

    async def fake_generate(db, last_hash=None):
        generation_count["n"] += 1
        return {
            "status": "generated",
            "hash": "hash1",
            "insights_new": 0,
            "insights_merged": 0,
            "skipped": False,
        }

    monkeypatch.setattr("app.insights.debouncer.generate_insights", fake_generate)

    debouncer = InsightDebouncer(debounce_seconds=0.1)
    debouncer.start()
    try:
        debouncer.schedule()
        await asyncio.sleep(0.02)
        debouncer.schedule()
        await asyncio.sleep(0.02)
        debouncer.schedule()

        # Wait past debounce window + generation
        await asyncio.sleep(0.5)

        assert generation_count["n"] == 1
        assert debouncer.last_hash == "hash1"
    finally:
        await debouncer.stop()


@pytest.mark.asyncio
async def test_debouncer_skips_when_lock_held(monkeypatch):
    """T30: debounce fires while lock already held -> silently skipped."""
    generation_count = {"n": 0}

    async def fake_generate(db, last_hash=None):
        generation_count["n"] += 1
        await asyncio.sleep(0.5)
        return {
            "status": "generated",
            "hash": "h",
            "insights_new": 0,
            "insights_merged": 0,
            "skipped": False,
        }

    monkeypatch.setattr("app.insights.debouncer.generate_insights", fake_generate)

    debouncer = InsightDebouncer(debounce_seconds=0.05)
    debouncer.start()
    try:
        # Acquire the lock externally to simulate an ongoing generation.
        await debouncer.lock.acquire()
        try:
            debouncer.schedule()
            await asyncio.sleep(0.2)  # past debounce timer
            # Lock held -> should be skipped silently; no generation run.
            assert generation_count["n"] == 0
        finally:
            debouncer.lock.release()
    finally:
        await debouncer.stop()


@pytest.mark.asyncio
async def test_debouncer_run_now_returns_result_and_updates_hash(monkeypatch):
    async def fake_generate(db, last_hash=None):
        return {
            "status": "generated",
            "hash": "abc",
            "insights_new": 2,
            "insights_merged": 1,
            "skipped": False,
        }

    monkeypatch.setattr("app.insights.debouncer.generate_insights", fake_generate)

    debouncer = InsightDebouncer(debounce_seconds=10)
    debouncer.start()
    try:
        result = await debouncer.run_now()
        assert result["status"] == "generated"
        assert debouncer.last_hash == "abc"
    finally:
        await debouncer.stop()


@pytest.mark.asyncio
async def test_debouncer_is_locked_reports_correctly():
    debouncer = InsightDebouncer(debounce_seconds=10)
    assert debouncer.is_locked() is False
    await debouncer.lock.acquire()
    try:
        assert debouncer.is_locked() is True
    finally:
        debouncer.lock.release()
    assert debouncer.is_locked() is False
