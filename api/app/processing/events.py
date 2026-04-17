import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Process-wide registry of per-paper asyncio.Events used to wake SSE
# listeners on status changes. Cleaned up by the SSE generator's finally
# block and by process_paper on terminal status; the cap below is a last
# resort to bound memory when clients vanish without closing the stream.
_paper_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)

_MAX_PAPER_EVENTS = 1000


def _purge_unwaited_events() -> int:
    """Drop events that have no task waiting on them. Returns count purged."""
    stale: list[str] = []
    for key, event in _paper_events.items():
        # asyncio.Event exposes an internal _waiters deque; empty means
        # no coroutine is currently parked on event.wait().
        waiters = getattr(event, "_waiters", None)
        if not waiters:
            stale.append(key)
    for key in stale:
        _paper_events.pop(key, None)
    return len(stale)


def notify_paper_update(paper_id: str):
    _paper_events[paper_id].set()
    if len(_paper_events) > _MAX_PAPER_EVENTS:
        purged = _purge_unwaited_events()
        logger.warning(
            "paper_events_cap_exceeded",
            extra={"total": len(_paper_events), "purged": purged, "cap": _MAX_PAPER_EVENTS},
        )


async def wait_for_update(paper_id: str, timeout: float = 2.0) -> bool:
    event = _paper_events[paper_id]
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        event.clear()
        return True
    except TimeoutError:
        return False


def cleanup_paper_event(paper_id: str):
    _paper_events.pop(paper_id, None)
