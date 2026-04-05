import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

# Point all engines (including the one created in app.core.database) to the test DB
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://synapseai:synapseai_test@localhost:5434/synapseai_test"
)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.database import get_db  # noqa: E402
from app.core.enums import PaperStatus, SourceType  # noqa: E402
from app.main import app  # noqa: E402
from app.papers.models import Paper  # noqa: E402

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
                "insight, insight_paper, chat_session, chat_message, processing_event "
                "CASCADE"
            )
        )
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
    """Factory fixture to create test papers in the DB."""

    async def _create(
        source_type: str = SourceType.PDF,
        status: str = PaperStatus.UPLOADING,
        **kwargs,
    ) -> Paper:
        paper = Paper(
            id=uuid.uuid4(),
            source_type=source_type,
            status=status,
            **kwargs,
        )
        db.add(paper)
        await db.flush()
        return paper

    return _create


@pytest.fixture
def tmp_upload_dir(tmp_path, monkeypatch):
    """Override UPLOAD_DIR to a temporary directory."""
    monkeypatch.setattr("app.config.upload_settings.UPLOAD_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def mock_claude(monkeypatch):
    """Patch asyncio.create_subprocess_exec to return a mock Claude CLI response."""
    summary_json = json.dumps({
        "result": json.dumps({
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
    })

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (summary_json.encode(), b"")
    mock_process.returncode = 0
    mock_process.kill = MagicMock()
    mock_process.wait = AsyncMock()

    async def mock_subprocess(*args, **kwargs):
        return mock_process

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subprocess)
    return mock_process
