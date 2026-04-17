import asyncio
import logging

logger = logging.getLogger(__name__)

_active_tasks: set[asyncio.Task] = set()
_shutting_down = False


class ShuttingDownError(RuntimeError):
    """Raised when launch_processing is called after shutdown has begun."""


def mark_shutting_down() -> None:
    """Flip the shutdown flag so no new background processing is accepted."""
    global _shutting_down
    _shutting_down = True


def launch_processing(coro) -> asyncio.Task:
    if _shutting_down:
        # Close the coroutine so asyncio doesn't emit a "coroutine was never
        # awaited" warning for work we refuse to run.
        coro.close()
        logger.warning("launch_processing_rejected_shutdown")
        raise ShuttingDownError("API is shutting down; processing refused")
    task = asyncio.create_task(coro)
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task


async def drain_tasks():
    """Cancel and await all active processing tasks. Called on shutdown."""
    for task in _active_tasks:
        task.cancel()
    await asyncio.gather(*_active_tasks, return_exceptions=True)
