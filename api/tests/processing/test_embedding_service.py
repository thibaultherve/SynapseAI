"""T22: Embedding service — encode text + batch (mock model)."""

import numpy as np
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from app.processing import embedding_service


@pytest.fixture(autouse=True)
def _reset_embedding_state():
    """Reset the embedding service global state before each test."""
    embedding_service._model = None
    embedding_service._executor = None
    yield
    embedding_service._model = None
    if embedding_service._executor is not None:
        embedding_service._executor.shutdown(wait=False)
        embedding_service._executor = None


def _make_mock_model():
    """Create a mock SentenceTransformer model."""
    model = MagicMock()

    def mock_encode(text_or_texts, **kwargs):
        if isinstance(text_or_texts, str):
            return np.random.rand(768).astype(np.float32)
        return np.random.rand(len(text_or_texts), 768).astype(np.float32)

    model.encode = mock_encode
    return model


@pytest.mark.asyncio
async def test_load_embedding_model():
    """Loading should set the global model and executor."""
    mock_model = _make_mock_model()

    with patch(
        "app.processing.embedding_service.SentenceTransformer",
        return_value=mock_model,
        create=True,
    ):
        # Patch the _load inner function by patching the import
        with patch.dict(
            "sys.modules",
            {"sentence_transformers": MagicMock(SentenceTransformer=lambda *a, **kw: mock_model)},
        ):
            await embedding_service.load_embedding_model()

    assert embedding_service._model is not None
    assert embedding_service._executor is not None


@pytest.mark.asyncio
async def test_encode_text_without_loaded_model_raises():
    """encode_text without loading model should raise RuntimeError."""
    with pytest.raises(RuntimeError, match="not loaded"):
        await embedding_service.encode_text("test text")


@pytest.mark.asyncio
async def test_encode_text_returns_vector():
    """encode_text should return a list of floats with correct dimension."""
    from concurrent.futures import ThreadPoolExecutor

    mock_model = _make_mock_model()
    embedding_service._model = mock_model
    embedding_service._executor = ThreadPoolExecutor(max_workers=1)

    result = await embedding_service.encode_text("test text")
    assert isinstance(result, list)
    assert len(result) == 768
    assert all(isinstance(v, float) for v in result)


@pytest.mark.asyncio
async def test_encode_batch_returns_vectors():
    """encode_batch should return a list of vectors for each input text."""
    from concurrent.futures import ThreadPoolExecutor

    mock_model = _make_mock_model()
    embedding_service._model = mock_model
    embedding_service._executor = ThreadPoolExecutor(max_workers=1)

    texts = ["First text", "Second text", "Third text"]
    result = await embedding_service.encode_batch(texts)

    assert isinstance(result, list)
    assert len(result) == 3
    for vec in result:
        assert len(vec) == 768


@pytest.mark.asyncio
async def test_encode_batch_empty_list():
    """encode_batch with empty list returns empty list."""
    result = await embedding_service.encode_batch([])
    assert result == []


@pytest.mark.asyncio
async def test_unload_clears_state():
    """unload_embedding_model should clear model and executor."""
    from concurrent.futures import ThreadPoolExecutor

    embedding_service._model = _make_mock_model()
    embedding_service._executor = ThreadPoolExecutor(max_workers=1)

    await embedding_service.unload_embedding_model()
    assert embedding_service._model is None
    assert embedding_service._executor is None
