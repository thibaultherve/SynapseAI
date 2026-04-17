"""Phase 10.4: verify 429 responses carry Retry-After and X-RateLimit-* headers.

Uses POST /api/insights/refresh (limited to 1/10minute) — the tightest rate
in the app, so only one call needs to succeed before the second is rejected.
insight_debouncer.run_now is patched to avoid invoking the real Claude CLI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_rate_limit_response_has_retry_after(client):
    async def _noop_run_now():
        return {
            "status": "skipped",
            "hash": "deadbeef",
            "insights_new": 0,
            "insights_merged": 0,
            "skipped": True,
        }

    with patch(
        "app.insights.router.insight_debouncer.run_now",
        new=AsyncMock(side_effect=_noop_run_now),
    ), patch(
        "app.insights.router.service.cleanup_orphan_insights",
        new=AsyncMock(return_value=0),
    ):
        first = await client.post("/api/insights/refresh")
        assert first.status_code == 200, first.text

        second = await client.post("/api/insights/refresh")
        assert second.status_code == 429

        header_keys = {k.lower() for k in second.headers.keys()}
        assert "retry-after" in header_keys

        retry_after = int(second.headers["retry-after"])
        # 1/10minute → 600 seconds window. Non-negative, reasonable bound.
        assert 0 < retry_after <= 600

        # Our handler also emits X-RateLimit-* based on view_rate_limit.
        assert "x-ratelimit-limit" in header_keys
        assert "x-ratelimit-remaining" in header_keys
        assert second.headers["x-ratelimit-remaining"] == "0"
        assert second.headers["x-ratelimit-limit"] == "1"

        body = second.json()
        assert body["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_retry_after_fallback_when_view_rate_limit_missing():
    """When slowapi didn't populate request.state.view_rate_limit, our handler
    must still emit a default Retry-After instead of crashing."""
    from slowapi.errors import RateLimitExceeded

    from app.main import _rate_limit_handler

    class _State:
        pass  # no view_rate_limit attribute

    class _FakeRequest:
        state = _State()

    class _FakeLimit:
        error_message = "fallback"
        amount = 1

        def get_expiry(self):
            return 60

    exc = RateLimitExceeded(limit=_FakeLimit())
    response = await _rate_limit_handler(_FakeRequest(), exc)

    assert response.status_code == 429
    assert response.headers.get("retry-after") == "60"
    # No X-RateLimit-* headers in fallback path (no rate-limit item known).
    assert "x-ratelimit-limit" not in {k.lower() for k in response.headers.keys()}
