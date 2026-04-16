import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.router import router as chat_router
from app.config import settings
from app.core.database import engine, get_db
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware, SSEAwareGZipMiddleware
from app.core.schemas import HealthResponse
from app.graph.router import router as graph_router
from app.insights.router import router as insights_router
from app.papers.router import router as papers_router
from app.processing.router import router as processing_router
from app.search.router import router as search_router
from app.tags.router import router as tags_router

configure_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


def _require_single_worker() -> None:
    # v1 relies on process-local state (rate limiter counters, insight
    # debouncer lock, _paper_events, claude semaphore). Multi-worker
    # deployments would desynchronize these, so crash loud at boot
    # rather than serve traffic with inconsistent limits.
    concurrency = os.environ.get("WEB_CONCURRENCY", "1")
    if concurrency != "1":
        raise RuntimeError(
            f"SynapseAI v1 requires WEB_CONCURRENCY=1 (got {concurrency!r}). "
            "Process-local state would desynchronize across workers."
        )


async def _startup_db_probe() -> None:
    # Three concurrent probes exercise the pool — catches pool-size
    # misconfigurations at boot instead of during the first real burst.
    async def _probe() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    await asyncio.gather(_probe(), _probe(), _probe())


@asynccontextmanager
async def lifespan(app: FastAPI):
    _require_single_worker()

    from app.insights.debouncer import insight_debouncer
    from app.processing.embedding_service import (
        load_embedding_model,
        unload_embedding_model,
    )
    from app.processing.task_registry import drain_tasks

    try:
        await _startup_db_probe()
        await load_embedding_model()
        insight_debouncer.start()
        logger.info("lifespan_started")

        yield

        await drain_tasks()
        await insight_debouncer.stop()
        await unload_embedding_model()
        logger.info("lifespan_stopped")
    except Exception:
        logger.exception("lifespan_failed")
        raise


_is_prod = settings.ENV == "production"

app = FastAPI(
    title="SynapseAI",
    openapi_url="/api/openapi.json" if not _is_prod else None,
    docs_url="/docs" if not _is_prod else None,
    redoc_url="/redoc" if not _is_prod else None,
    lifespan=lifespan,
)

# Middleware order matters — last added = outermost (runs first per request).
# We want: RequestId outermost so every request, including CORS preflights,
# gets a correlation id. GZip innermost so it sees the final response body.
app.add_middleware(SSEAwareGZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestIdMiddleware)

from app.ratelimit import limiter  # noqa: E402

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error",
            }
        },
    )


app.include_router(papers_router)
app.include_router(processing_router)
app.include_router(tags_router)
app.include_router(search_router)
app.include_router(chat_router)
app.include_router(graph_router)
app.include_router(insights_router)


@app.get(
    "/api/health",
    response_model=HealthResponse,
    status_code=200,
    description="Check API and database connectivity.",
    tags=["health"],
)
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
