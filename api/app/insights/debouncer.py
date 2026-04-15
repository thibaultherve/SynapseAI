import asyncio
import contextlib
import logging

from app.config import insight_settings
from app.core.database import async_session
from app.insights.service import generate_insights

logger = logging.getLogger(__name__)


class InsightDebouncer:
    """Debounce insight generation events.

    - `schedule()` resets a timer: generation fires once after
      `INSIGHT_DEBOUNCE_SECONDS` of silence.
    - A global `asyncio.Lock` guarantees at most one generation runs at a time.
    - When the debounce timer fires while the lock is already held, the run is
      silently skipped (per spec §3.3).
    - `_last_hash` is kept in-memory and used by `generate_insights` for
      idempotence.
    """

    def __init__(self, debounce_seconds: float | None = None):
        self._debounce_seconds = (
            debounce_seconds
            if debounce_seconds is not None
            else float(insight_settings.INSIGHT_DEBOUNCE_SECONDS)
        )
        self._pending_task: asyncio.Task | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._last_hash: str | None = None
        self._started = False

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def last_hash(self) -> str | None:
        return self._last_hash

    def is_locked(self) -> bool:
        return self._lock.locked()

    def start(self) -> None:
        """Mark the debouncer as active (called from lifespan startup)."""
        self._started = True

    async def stop(self) -> None:
        """Cancel any pending debounce timer (called from lifespan shutdown)."""
        self._started = False
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pending_task
        self._pending_task = None

    def reset(self) -> None:
        """Reset in-memory state. Safe to call between tests to avoid
        event-loop / hash / lock contamination across pytest-asyncio loops.
        """
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
        self._pending_task = None
        self._last_hash = None
        self._lock = asyncio.Lock()

    def schedule(self) -> None:
        """Reset the debounce timer. Safe to call from sync contexts inside an event loop."""
        if not self._started:
            return
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
        self._pending_task = asyncio.create_task(self._run_after_delay())

    async def run_now(self) -> dict:
        """Execute a generation synchronously (used by POST /insights/refresh).

        Caller MUST check `is_locked()` first to return 409. This method
        acquires the lock and returns the result dict from `generate_insights`.
        Updates `_last_hash` on success. Wraps generation in an upper-bound
        timeout so a hung Claude CLI subprocess can't pin the lock forever.
        """
        timeout = (
            insight_settings.INSIGHT_CLAUDE_TIMEOUT
            + insight_settings.INSIGHT_GENERATION_TIMEOUT_MARGIN
        )
        async with self._lock, async_session() as db:
            result = await asyncio.wait_for(
                generate_insights(db, last_hash=self._last_hash),
                timeout=timeout,
            )
        if result.get("hash"):
            self._last_hash = result["hash"]
        return result

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    async def _run_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._debounce_seconds)
        except asyncio.CancelledError:
            return

        if self._lock.locked():
            # Another generation is already running; silently skip.
            logger.info("insight_debouncer_skipped_locked")
            return

        timeout = (
            insight_settings.INSIGHT_CLAUDE_TIMEOUT
            + insight_settings.INSIGHT_GENERATION_TIMEOUT_MARGIN
        )
        try:
            async with self._lock, async_session() as db:
                result = await asyncio.wait_for(
                    generate_insights(db, last_hash=self._last_hash),
                    timeout=timeout,
                )
            if result.get("hash"):
                self._last_hash = result["hash"]
        except TimeoutError:
            logger.error("insight_debouncer_generation_timed_out")
        except Exception:  # noqa: BLE001
            logger.exception("insight_debouncer_generation_failed")


# Module-level singleton. Initialized/torn down from the FastAPI lifespan.
insight_debouncer = InsightDebouncer()
