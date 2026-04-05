import asyncio
from collections import defaultdict

_paper_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)


def notify_paper_update(paper_id: str):
    _paper_events[paper_id].set()


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
