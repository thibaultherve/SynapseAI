import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.enums import SourceType, StepName
from app.main import app
from app.papers.models import Paper
from app.processing.models import PaperStep

from tests.conftest import override_get_db


@pytest.mark.asyncio
async def test_unhandled_exception_returns_500(db):
    """When an unhandled exception occurs in a route -> 500 INTERNAL_ERROR."""
    paper_id = uuid.uuid4()
    paper = Paper(id=paper_id, source_type=SourceType.PDF)
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch(
                "app.papers.service.delete_paper",
                new_callable=AsyncMock,
                side_effect=RuntimeError("unexpected boom"),
            ):
                response = await ac.delete(f"/api/papers/{paper_id}")

        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"
        assert data["error"]["message"] == "Internal server error"
    finally:
        app.dependency_overrides.clear()
