import asyncio

_active_tasks: set[asyncio.Task] = set()


def launch_processing(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task


async def drain_tasks():
    """Cancel and await all active processing tasks. Called on shutdown."""
    for task in _active_tasks:
        task.cancel()
    await asyncio.gather(*_active_tasks, return_exceptions=True)
