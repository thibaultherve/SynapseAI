import atexit
import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

# Point all engines (including the one created in app.core.database) to the test DB
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://synapseai:synapseai_test@localhost:5434/synapseai_test"
)

# Mock embedding model loading before importing app (lifespan calls load_embedding_model).
# Module-level patches are required because the lifespan runs when the ASGI
# test client starts, before per-test fixtures are available.
# Tests that exercise the real load/unload flow (tests/processing/test_embedding_service.py)
# can call `pause_embedding_lifecycle_mocks()` / `resume_embedding_lifecycle_mocks()` to
# temporarily restore the real functions.
_load_patch = patch(
    "app.processing.embedding_service.load_embedding_model",
    new_callable=AsyncMock,
)
_unload_patch = patch(
    "app.processing.embedding_service.unload_embedding_model",
    new_callable=AsyncMock,
)
_load_patch.start()
_unload_patch.start()
atexit.register(_load_patch.stop)
atexit.register(_unload_patch.stop)

_embedding_lifecycle_mocks_active = True


def pause_embedding_lifecycle_mocks() -> None:
    global _embedding_lifecycle_mocks_active
    if _embedding_lifecycle_mocks_active:
        _load_patch.stop()
        _unload_patch.stop()
        _embedding_lifecycle_mocks_active = False


def resume_embedding_lifecycle_mocks() -> None:
    global _embedding_lifecycle_mocks_active
    if not _embedding_lifecycle_mocks_active:
        _load_patch.start()
        _unload_patch.start()
        _embedding_lifecycle_mocks_active = True

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.database import get_db  # noqa: E402
from app.core.enums import SourceType, StepName  # noqa: E402
from app.main import app  # noqa: E402
from app.papers.models import Paper  # noqa: E402
from app.processing.models import (  # noqa: E402
    CrossReference,
    PaperEmbedding,
    PaperStep,
)

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

test_engine = create_async_engine(TEST_DATABASE_URL)
test_session = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture(autouse=True, scope="function")
async def _clean_tables():
    """Truncate all data tables before each test for isolation."""
    async with test_engine.begin() as conn:
        await conn.execute(
            sa_text(
                "TRUNCATE paper, tag, paper_tag, paper_embedding, cross_reference, "
                "insight, insight_paper, chat_session, chat_message, processing_event, "
                "paper_step "
                "CASCADE"
            )
        )
    # Reset in-memory rate-limit counters so per-test quotas don't leak.
    from app.ratelimit import limiter
    limiter.reset()
    # Reset the insight debouncer singleton so its lock/hash/task don't
    # leak across tests (and across different pytest-asyncio event loops).
    from app.insights.debouncer import insight_debouncer
    insight_debouncer.reset()
    yield


async def override_get_db():
    async with test_session() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
async def client():
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def db():
    async with test_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def paper_factory(db):
    """Factory fixture to create test papers with steps in the DB.

    Args:
        source_type: PDF or WEB
        steps: Optional dict mapping step name -> status string.
               Missing steps default to "pending".
        **kwargs: Extra Paper field overrides.
    """

    async def _create(
        source_type: str = SourceType.PDF,
        steps: dict[str, str] | None = None,
        **kwargs,
    ) -> Paper:
        paper = Paper(
            id=uuid.uuid4(),
            source_type=source_type,
            **kwargs,
        )
        db.add(paper)
        await db.flush()

        step_statuses = steps or {}
        for step_name in StepName:
            db.add(PaperStep(
                paper_id=paper.id,
                step=step_name.value,
                status=step_statuses.get(step_name.value, "pending"),
            ))
        await db.commit()

        return paper

    return _create


@pytest.fixture
def tmp_upload_dir(tmp_path, monkeypatch):
    """Override UPLOAD_DIR to a temporary directory."""
    monkeypatch.setattr("app.config.upload_settings.UPLOAD_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def mock_claude(monkeypatch):
    """Patch asyncio.create_subprocess_exec to return a mock Claude CLI response.

    Emits the list-envelope shape (`[{type: assistant, message: {content: [...]}}]`)
    that `call_claude` now parses — matches what `claude -p --output-format json`
    actually produces.
    """
    summary_text = json.dumps({
        "title": "Test Paper Title",
        "authors": ["Author One", "Author Two"],
        "authors_short": "One et al.",
        "publication_date": "2024-01-15",
        "journal": "Nature",
        "doi": None,
        "short_summary": "This is a short summary of the test paper.",
        "detailed_summary": "This is a detailed summary with sections.",
        "key_findings": "1. Finding one\n2. Finding two",
        "keywords": ["ai", "neuroscience"],
    })
    claude_output = json.dumps([
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": summary_text}]},
        }
    ])

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (claude_output.encode(), b"")
    mock_process.returncode = 0
    mock_process.kill = MagicMock()
    mock_process.wait = AsyncMock()

    async def mock_subprocess(*args, **kwargs):
        return mock_process

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess)
    return mock_process


@pytest.fixture
def mock_embedding(monkeypatch):
    """Mock the embedding service encode_batch/encode_text to return fake 768-dim vectors.

    Patches every module that imports encode_* from embedding_service — keep this list
    in sync with `grep -rn "from app.processing.embedding_service import" app/`.
    """
    FAKE_DIM = 768

    async def fake_encode_batch(texts):
        return [[0.1] * FAKE_DIM for _ in texts]

    async def fake_encode_text(text):
        return [0.1] * FAKE_DIM

    monkeypatch.setattr(
        "app.processing.service.encode_batch", fake_encode_batch
    )
    monkeypatch.setattr(
        "app.search.service.encode_text", fake_encode_text
    )
    monkeypatch.setattr(
        "app.chat.service.encode_text", fake_encode_text
    )


@pytest.fixture
async def crossref_factory(db):
    """Factory fixture to create cross_reference rows with canonical ordering."""

    async def _create(
        paper_a_id: uuid.UUID,
        paper_b_id: uuid.UUID,
        *,
        relation_type: str = "thematic",
        strength: str = "moderate",
        description: str | None = "Test crossref",
    ) -> CrossReference:
        # Enforce paper_a < paper_b CHECK constraint
        a, b = sorted([paper_a_id, paper_b_id], key=str)
        row = CrossReference(
            paper_a=a,
            paper_b=b,
            relation_type=relation_type,
            strength=strength,
            description=description,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    return _create


@pytest.fixture
async def embedding_factory(db):
    """Factory fixture to create test paper_embedding rows."""

    async def _create(paper_id: uuid.UUID, chunks: list[str] | None = None):
        if chunks is None:
            chunks = ["Test chunk content for embedding."]
        for i, chunk in enumerate(chunks):
            emb = PaperEmbedding(
                paper_id=paper_id,
                chunk_index=i,
                chunk_text=chunk,
                embedding=[0.1 + i * 0.01] * 768,
            )
            db.add(emb)
        await db.commit()

    return _create
