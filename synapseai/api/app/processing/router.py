import asyncio
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session, get_db
from app.core.enums import PaperStatus
from app.core.exceptions import AppError, ConflictError
from app.core.schemas import ErrorResponse
from app.papers.dependencies import get_paper_or_404
from app.papers.models import Paper
from app.papers.schemas import PaperResponse
from app.processing.constants import ErrorCode
from app.processing.events import cleanup_paper_event, wait_for_update
from app.processing.models import ProcessingEvent
from app.processing.service import process_paper
from app.processing.task_registry import launch_processing
from app.ratelimit import limiter

router = APIRouter(prefix="/api/papers", tags=["processing"])

_sse_connections: dict[str, int] = defaultdict(int)
MAX_SSE_PER_PAPER = 3
MAX_SSE_TOTAL = 50
SSE_MAX_DURATION = 600  # 10 minutes
SSE_HEARTBEAT_INTERVAL = 15

TERMINAL_STATUSES = {"summarized", "error", "deleted"}


@router.get(
    "/{paper_id}/status",
    description="Stream processing events via Server-Sent Events.",
    responses={
        404: {"model": ErrorResponse, "description": "Paper not found"},
        429: {"model": ErrorResponse, "description": "Too many SSE listeners for this paper"},
        503: {"model": ErrorResponse, "description": "Server at SSE capacity"},
    },
)
async def paper_status_stream(paper: Paper = Depends(get_paper_or_404)):
    paper_id = paper.id
    key = str(paper_id)

    total = sum(_sse_connections.values())
    if total >= MAX_SSE_TOTAL:
        raise AppError(ErrorCode.TOO_MANY_CONNECTIONS, "Server at SSE capacity", 503)
    if _sse_connections[key] >= MAX_SSE_PER_PAPER:
        raise AppError(ErrorCode.TOO_MANY_CONNECTIONS, "Too many listeners for this paper", 429)

    async def event_generator():
        _sse_connections[key] += 1
        try:
            start = asyncio.get_running_loop().time()
            last_event_id = 0

            while True:
                elapsed = asyncio.get_running_loop().time() - start
                if elapsed > SSE_MAX_DURATION:
                    yield "event: timeout\ndata: {}\n\n"
                    return

                # Fetch new events from DB
                async with async_session() as sse_db:
                    result = await sse_db.execute(
                        select(ProcessingEvent)
                        .where(ProcessingEvent.paper_id == paper_id)
                        .where(ProcessingEvent.id > last_event_id)
                        .order_by(ProcessingEvent.id)
                    )
                    events = result.scalars().all()

                    for event in events:
                        data = json.dumps({
                            "step": event.step,
                            "detail": event.detail,
                            "timestamp": event.created_at.isoformat(),
                        })
                        yield f"data: {data}\n\n"
                        last_event_id = event.id

                    # Check terminal state
                    current_paper = await sse_db.get(Paper, paper_id)
                    if current_paper and current_paper.status in TERMINAL_STATUSES:
                        data = json.dumps({"type": "complete", "status": current_paper.status})
                        yield f"data: {data}\n\n"
                        cleanup_paper_event(key)
                        return

                # Wait for notification or timeout (heartbeat)
                updated = await wait_for_update(key, timeout=SSE_HEARTBEAT_INTERVAL)
                if not updated:
                    yield ": keepalive\n\n"
        finally:
            _sse_connections[key] -= 1
            if _sse_connections[key] <= 0:
                _sse_connections.pop(key, None)
                cleanup_paper_event(key)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{paper_id}/retry",
    response_model=PaperResponse,
    description="Retry processing for a paper in error state.",
    responses={
        404: {"model": ErrorResponse, "description": "Paper not found"},
        409: {"model": ErrorResponse, "description": "Not in error state"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("5/minute")
async def retry_processing(
    request: Request,
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
):
    if paper.status != PaperStatus.ERROR:
        raise ConflictError(
            ErrorCode.INVALID_STATE,
            f"Cannot retry paper in '{paper.status}' state. Only 'error' papers can be retried.",
        )

    paper.status = PaperStatus.UPLOADING.value
    paper.error_message = None
    await db.flush()
    await db.refresh(paper)

    launch_processing(process_paper(paper.id))
    return paper
