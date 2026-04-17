"""Phase 10.4: startup healthcheck smoke tests.

Covers `_startup_db_probe` (pool concurrency probe) and
`_startup_check_pgvector_index` (HNSW index presence warning).
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import _startup_check_pgvector_index, _startup_db_probe


@pytest.mark.asyncio
async def test_db_probe_executes_three_concurrent_queries():
    # Real test DB — if pool is misconfigured, asyncio.gather of 3 probes
    # would block or error. This is the same path the lifespan runs.
    await _startup_db_probe()


@pytest.mark.asyncio
async def test_pgvector_index_check_no_warning_when_present(caplog):
    caplog.set_level(logging.WARNING, logger="app.main")
    await _startup_check_pgvector_index()
    assert not any(
        "pgvector_hnsw_index_missing" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_pgvector_index_check_warns_when_missing(caplog):
    # Simulate a DB where the HNSW index was never created (e.g. operator
    # booted the API against an un-migrated database).
    fake_result = MagicMock()
    fake_result.scalar.return_value = None

    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(return_value=fake_result)

    class _FakeConnCtx:
        async def __aenter__(self):
            return fake_conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.main.engine") as mock_engine:
        mock_engine.connect = MagicMock(return_value=_FakeConnCtx())
        caplog.set_level(logging.WARNING, logger="app.main")
        await _startup_check_pgvector_index()

    messages = [r.message for r in caplog.records]
    assert any("pgvector_hnsw_index_missing" in m for m in messages)


@pytest.mark.asyncio
async def test_pgvector_index_check_swallows_db_errors(caplog):
    # A transient DB failure during the check must NOT crash startup.
    class _RaisingCtx:
        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.main.engine") as mock_engine:
        mock_engine.connect = MagicMock(return_value=_RaisingCtx())
        caplog.set_level(logging.ERROR, logger="app.main")
        # Must not raise — check is best-effort.
        await _startup_check_pgvector_index()

    assert any(
        "pgvector_hnsw_index_check_failed" in r.message
        for r in caplog.records
    )
