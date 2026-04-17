"""In-process event bus for cross-feature notifications.

Design choices this module locks in:

- **Typed events**: events are :class:`Event` (``StrEnum``) members, not raw
  strings — typo-squatting at the ``subscribe`` / ``publish`` call site is
  impossible (mypy will catch it).
- **Sequential dispatch**: :func:`publish` awaits handlers **one at a time**
  in subscription order. Switching to ``asyncio.gather`` would be a
  behavior change (handlers may currently assume they do not race each
  other). Callers that need fan-out parallelism should schedule their own
  tasks inside the handler.
- **Per-handler timeout**: every handler is wrapped in
  ``asyncio.wait_for(..., timeout=HANDLER_TIMEOUT_S)``. A stuck handler
  cannot block the publisher.
- **Payload secrecy**: when a handler fails or times out, the payload is
  **never** included in the log record — only the event name and the
  handler's qualified name. This keeps PII and large blobs out of the
  observability pipeline.
- **Idempotent subscription**: subscribing the same ``(event, handler)``
  pair twice logs a warning and is a no-op; this makes lifespan/startup
  wiring safe to call more than once (e.g. tests that re-enter lifespan).

Only one event is defined today — :attr:`Event.PAPER_PROCESSED`,
published by the processing pipeline and consumed by the insight
debouncer to break the ``processing -> insights`` import cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[None]]

HANDLER_TIMEOUT_S: float = 5.0


class Event(StrEnum):
    PAPER_PROCESSED = "paper.processed"


_subs: dict[Event, list[Handler]] = {}


def subscribe(event: Event, handler: Handler) -> None:
    """Register ``handler`` to be awaited when ``event`` is published.

    Duplicate ``(event, handler)`` pairs are rejected with a warning log
    instead of being registered twice — callers can safely re-run
    subscription code on lifespan restart without building up duplicate
    dispatches.
    """
    handlers = _subs.setdefault(event, [])
    if handler in handlers:
        logger.warning(
            "event_handler_duplicate_subscription",
            extra={"event": event.value, "handler": handler.__qualname__},
        )
        return
    handlers.append(handler)


async def publish(event: Event, **payload: Any) -> None:
    """Dispatch ``event`` to every subscribed handler, sequentially.

    Each handler is awaited in isolation: a failing or timing-out handler
    is logged via ``logger.exception("event_handler_failed", ...)`` with
    **only** the event name and handler qualname in ``extra`` — the
    payload is deliberately withheld from logs. The publisher then moves
    on to the next handler so a broken subscriber cannot silence the
    others.
    """
    handlers = _subs.get(event, ())
    for handler in handlers:
        try:
            await asyncio.wait_for(handler(**payload), timeout=HANDLER_TIMEOUT_S)
        except Exception:
            logger.exception(
                "event_handler_failed",
                extra={
                    "event": event.value,
                    "handler": handler.__qualname__,
                },
            )


__all__ = (
    "Event",
    "HANDLER_TIMEOUT_S",
    "Handler",
    "publish",
    "subscribe",
)
