"""Singleton embedding model (nomic-embed-text) with ThreadPoolExecutor."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from app.config import embedding_settings

logger = logging.getLogger(__name__)

_model = None
_executor: ThreadPoolExecutor | None = None


def _get_model():
    if _model is None:
        raise RuntimeError("Embedding model not loaded. Call load_embedding_model() first.")
    return _model


async def load_embedding_model() -> None:
    """Load the embedding model at startup. Call from FastAPI lifespan."""
    global _model, _executor

    if _model is not None:
        return

    start = time.monotonic()
    logger.info("Loading embedding model: %s", embedding_settings.EMBEDDING_MODEL_NAME)

    loop = asyncio.get_running_loop()
    _executor = ThreadPoolExecutor(
        max_workers=embedding_settings.EMBEDDING_MAX_WORKERS,
        thread_name_prefix="embedding",
    )

    def _load():
        from sentence_transformers import SentenceTransformer

        # trust_remote_code required by nomic-embed-text-v1.5 for custom pooling.
        return SentenceTransformer(
            embedding_settings.EMBEDDING_MODEL_NAME,
            trust_remote_code=True,
        )

    _model = await loop.run_in_executor(_executor, _load)
    elapsed = time.monotonic() - start
    logger.info("Embedding model loaded in %.1fs", elapsed)


async def unload_embedding_model() -> None:
    """Shutdown executor. Call from lifespan shutdown."""
    global _model, _executor
    if _executor is not None:
        _executor.shutdown(wait=False)
        _executor = None
    _model = None


async def encode_text(text: str) -> list[float]:
    """Encode a single text into a vector."""
    model = _get_model()
    loop = asyncio.get_running_loop()

    def _encode():
        return model.encode(text, normalize_embeddings=True).tolist()

    return await loop.run_in_executor(_executor, _encode)


async def encode_batch(texts: list[str]) -> list[list[float]]:
    """Encode multiple texts into vectors."""
    if not texts:
        return []

    model = _get_model()
    loop = asyncio.get_running_loop()
    batch_size = embedding_settings.EMBEDDING_BATCH_SIZE

    def _encode():
        return model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
        ).tolist()

    return await loop.run_in_executor(_executor, _encode)
