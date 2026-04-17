import asyncio
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session, get_db
from app.core.enums import DerivedPaperStatus, StepName, StepStatus
from app.core.exceptions import ConflictError, ValidationError
from app.core.schemas import ErrorResponse
from app.papers.dependencies import get_paper_or_404
from app.papers.models import Paper
from app.papers.schemas import PaperStepResponse
from app.papers.utils import compute_paper_status
from app.processing.constants import ErrorCode
from app.processing.events import cleanup_paper_event, wait_for_update
from app.processing.models import PaperStep, ProcessingEvent
from app.processing.service import can_retry, process_paper, reset_step_for_retry
from app.processing.task_registry import launch_processing
from app.ratelimit import limiter

router = APIRouter(prefix="/api/papers", tags=["processing"])

_sse_connections: dict[str, int] = defaultdict(int)
MAX_SSE_PER_PAPER = 3
MAX_SSE_TOTAL = 50
SSE_MAX_DURATION = 600  # 10 minutes
SSE_HEARTBEAT_INTERVAL = 15

TERMINAL_STATUSES = {
    DerivedPaperStatus.READABLE,
    DerivedPaperStatus.ENRICHED,
    DerivedPaperStatus.ERROR,
}


@router.get(
    "/{paper_id}/status",
    status_code=200,
    description="Stream processing events via Server-Sent Events.",
    responses={
        404: {"model": ErrorResponse, "description": "Paper not found"},
        429: {"model": ErrorResponse, "description": "Too many SSE listeners for this paper"},
        503: {"model": ErrorResponse, "description": "Server at SSE capacity"},
    },
)
async def paper_status_stream(paper: Paper = Depends(get_paper_or_404)):
    from app.core.exceptions import AppError

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

                    # Check terminal state via computed status from steps
                    steps = (await sse_db.execute(
                        select(PaperStep).where(PaperStep.paper_id == paper_id)
                    )).scalars().all()
                    computed = compute_paper_status(steps)
                    if computed in TERMINAL_STATUSES:
                        data = json.dumps({"type": "complete", "status": computed})
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


# ---------------------------------------------------------------------------
# GET /api/papers/:id/steps
# ---------------------------------------------------------------------------


@router.get(
    "/{paper_id}/steps",
    status_code=200,
    response_model=list[PaperStepResponse],
    description="List all processing steps for a paper.",
    responses={404: {"model": ErrorResponse, "description": "Paper not found"}},
)
async def get_paper_steps(paper: Paper = Depends(get_paper_or_404)):
    return paper.steps


# ---------------------------------------------------------------------------
# POST /api/papers/:id/retry/:step
# ---------------------------------------------------------------------------


@router.post(
    "/{paper_id}/retry/{step_name}",
    status_code=200,
    response_model=PaperStepResponse,
    description="Retry a single processing step that is in error state.",
    responses={
        404: {"model": ErrorResponse, "description": "Paper not found"},
        409: {"model": ErrorResponse, "description": "Step not in error state"},
        422: {"model": ErrorResponse, "description": "Invalid step or prerequisites not met"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("5/minute")
async def retry_step(
    request: Request,
    step_name: StepName = Path(..., description="Step name to retry"),
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
):
    # Find the step
    step = next((s for s in paper.steps if s.step == step_name.value), None)
    if not step:
        raise ValidationError(
            ErrorCode.INVALID_STEP, f"Step '{step_name.value}' not found for this paper"
        )

    # Check step is in error
    if step.status != StepStatus.ERROR:
        raise ConflictError(
            ErrorCode.STEP_NOT_IN_ERROR,
            f"Cannot retry step '{step_name.value}' in '{step.status}' state. "
            "Only steps in 'error' state can be retried.",
        )

    # Check preconditions
    ok, reason = can_retry(paper, step_name.value)
    if not ok:
        raise ValidationError(ErrorCode.RETRY_PRECONDITION_FAILED, reason)

    await reset_step_for_retry(db, step)

    launch_processing(process_paper(paper.id))
    return step
