import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

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


async def _startup_check_pgvector_index() -> None:
    # Warn (never fail) if the HNSW index is missing. Similarity search
    # falls back to a seq scan of paper_embedding, which is functional
    # but slow — operators should run `alembic upgrade head`.
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND tablename = 'paper_embedding' "
                    "AND indexname = 'idx_embeddings_vec'"
                )
            )
            if result.scalar() is None:
                logger.warning(
                    "pgvector_hnsw_index_missing",
                    extra={
                        "table": "paper_embedding",
                        "expected_index": "idx_embeddings_vec",
                        "remediation": "alembic upgrade head",
                    },
                )
    except Exception:
        logger.exception("pgvector_hnsw_index_check_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _require_single_worker()

    from app.core.embedding_client import (
        load_embedding_model,
        unload_embedding_model,
    )
    from app.insights.debouncer import insight_debouncer
    from app.processing.task_registry import drain_tasks, mark_shutting_down

    try:
        await _startup_db_probe()
        await _startup_check_pgvector_index()
        await load_embedding_model()
        insight_debouncer.start()
        logger.info("lifespan_started")

        yield

        # Flip the shutdown flag BEFORE draining so any late arrival is
        # refused instead of racing against cancellation mid-await.
        mark_shutting_down()
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


async def _rate_limit_handler(
    request: Request, exc: RateLimitExceeded
) -> Response:
    # slowapi's default handler does not emit Retry-After unless
    # headers_enabled=True on the Limiter — and that flag breaks endpoints
    # that return Pydantic objects. So we emit Retry-After + X-RateLimit-*
    # ourselves, deriving the window from request.state.view_rate_limit.
    retry_after = 60
    limit_amount: str | None = None
    try:
        view_limit = getattr(request.state, "view_rate_limit", None)
        if view_limit is not None:
            limit_item = view_limit[0]
            retry_after = int(limit_item.get_expiry())
            limit_amount = str(limit_item.amount)
    except Exception:
        logger.exception("rate_limit_header_derivation_failed")

    headers: dict[str, str] = {"Retry-After": str(retry_after)}
    if limit_amount is not None:
        headers["X-RateLimit-Limit"] = limit_amount
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = str(retry_after)

    return JSONResponse(
        status_code=429,
        headers=headers,
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": f"Rate limit exceeded: {exc.detail}",
            }
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)


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
