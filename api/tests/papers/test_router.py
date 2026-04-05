import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import SourceType

# --- Upload PDF ---


@pytest.mark.asyncio
async def test_upload_pdf_valid(client, tmp_upload_dir):
    """Upload a valid PDF -> 201, paper created with steps."""
    pdf_content = b"%PDF-1.4 fake pdf content for testing"

    with patch("app.processing.task_registry.launch_processing"):
        response = await client.post(
            "/api/papers/upload",
            files={"file": ("test.pdf", BytesIO(pdf_content), "application/pdf")},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["source_type"] == "pdf"
    assert data["status"] == "pending"
    assert data["id"] is not None
    assert len(data["steps"]) == 6


@pytest.mark.asyncio
async def test_upload_pdf_too_large(client, tmp_upload_dir, monkeypatch):
    """Upload a file exceeding size limit -> 413."""
    monkeypatch.setattr("app.config.upload_settings.UPLOAD_MAX_SIZE", 100)
    pdf_content = b"%PDF-" + b"x" * 200

    response = await client.post(
        "/api/papers/upload",
        files={"file": ("big.pdf", BytesIO(pdf_content), "application/pdf")},
    )

    assert response.status_code == 413
    assert "FILE_TOO_LARGE" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_upload_pdf_invalid_mime(client, tmp_upload_dir):
    """Upload a non-PDF file -> 422."""
    response = await client.post(
        "/api/papers/upload",
        files={"file": ("test.txt", BytesIO(b"not a pdf"), "text/plain")},
    )

    assert response.status_code == 422
    assert "INVALID_FILE_TYPE" in response.json()["error"]["code"]


# --- Create from URL / DOI ---


@pytest.mark.asyncio
async def test_create_paper_url_valid(client):
    """POST /api/papers with valid URL -> 201."""
    with (
        patch(
            "app.papers.service.validate_url",
            new_callable=AsyncMock,
            return_value="https://example.com/paper",
        ),
        patch("app.processing.task_registry.launch_processing"),
    ):
        response = await client.post(
            "/api/papers",
            json={"url": "https://example.com/paper"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["source_type"] == "web"
    assert data["url"] == "https://example.com/paper"


@pytest.mark.asyncio
async def test_create_paper_doi_valid(client):
    """POST /api/papers with valid DOI -> 201."""
    with (
        patch(
            "app.papers.service.resolve_doi",
            new_callable=AsyncMock,
            return_value="https://nature.com/article",
        ),
        patch("app.processing.task_registry.launch_processing"),
    ):
        response = await client.post(
            "/api/papers",
            json={"doi": "10.1038/s41586-024-00001"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["doi"] == "10.1038/s41586-024-00001"
    assert data["source_type"] == "web"


@pytest.mark.asyncio
async def test_create_paper_doi_duplicate(client, db):
    """POST /api/papers with duplicate DOI -> 409."""
    from app.core.enums import StepName
    from app.papers.models import Paper
    from app.processing.models import PaperStep

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        source_type=SourceType.WEB,
        doi="10.1038/s41586-024-99999",
        url="https://example.com",
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    with patch(
        "app.papers.service.resolve_doi",
        new_callable=AsyncMock,
        return_value="https://example.com",
    ):
        response = await client.post(
            "/api/papers",
            json={"doi": "10.1038/s41586-024-99999"},
        )

    assert response.status_code == 409
    assert "DUPLICATE_DOI" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_create_paper_no_url_no_doi(client):
    """POST /api/papers with neither URL nor DOI -> 422."""
    response = await client.post("/api/papers", json={})

    assert response.status_code == 422


# --- CRUD ---


@pytest.mark.asyncio
async def test_list_papers(client, paper_factory):
    """GET /api/papers -> list of papers."""
    for _i in range(3):
        await paper_factory()
    # paper_factory uses its own db session; commit to make visible to client
    # (paper_factory already flushes, but client uses a different session)

    response = await client.get("/api/papers")

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 3


@pytest.mark.asyncio
async def test_get_paper_detail(client, paper_factory):
    """GET /api/papers/:id -> paper detail with steps."""
    paper = await paper_factory(title="Test Paper")

    response = await client.get(f"/api/papers/{paper.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Paper"
    assert data["status"] == "pending"
    assert len(data["steps"]) == 6


@pytest.mark.asyncio
async def test_get_paper_not_found(client):
    """GET /api/papers/:id with bad id -> 404."""
    fake_id = uuid.uuid4()
    response = await client.get(f"/api/papers/{fake_id}")

    assert response.status_code == 404
    assert "PAPER_NOT_FOUND" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_delete_paper(client, paper_factory):
    """DELETE /api/papers/:id -> 204."""
    paper = await paper_factory()

    response = await client.delete(f"/api/papers/{paper.id}")

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_update_paper_metadata(client, paper_factory):
    """PATCH /api/papers/:id -> update metadata."""
    paper = await paper_factory()

    response = await client.patch(
        f"/api/papers/{paper.id}",
        json={"title": "Updated Title", "journal": "Science"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["journal"] == "Science"


# --- File download ---


@pytest.mark.asyncio
async def test_get_paper_file_ok(client, db, tmp_upload_dir):
    """GET /api/papers/:id/file -> download PDF."""
    from app.core.enums import StepName
    from app.papers.models import Paper
    from app.processing.models import PaperStep

    paper_id = uuid.uuid4()
    file_path = Path(tmp_upload_dir) / f"{paper_id}.pdf"
    file_path.write_bytes(b"%PDF-1.4 test content")

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        file_path=str(file_path),
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    response = await client.get(f"/api/papers/{paper_id}/file")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_get_paper_file_no_file(client, paper_factory):
    """GET /api/papers/:id/file with no file_path -> 404."""
    paper = await paper_factory(source_type=SourceType.WEB)

    response = await client.get(f"/api/papers/{paper.id}/file")

    assert response.status_code == 404
    assert "NO_FILE" in response.json()["error"]["code"]


# --- Pagination ---


@pytest.mark.asyncio
async def test_list_papers_pagination(client, paper_factory):
    """GET /api/papers?skip=1&limit=2 -> paginated results."""
    for _i in range(5):
        await paper_factory()

    response = await client.get("/api/papers?skip=1&limit=2")

    assert response.status_code == 200
    assert len(response.json()) == 2


# --- Edge cases: file download ---


@pytest.mark.asyncio
async def test_get_paper_file_deleted_from_disk(client, db, tmp_upload_dir):
    """GET /api/papers/:id/file when file is missing on disk -> 404."""
    from app.core.enums import StepName
    from app.papers.models import Paper
    from app.processing.models import PaperStep

    paper_id = uuid.uuid4()
    file_path = Path(tmp_upload_dir) / f"{paper_id}.pdf"

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        file_path=str(file_path),
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    response = await client.get(f"/api/papers/{paper_id}/file")

    assert response.status_code == 404
    assert "NO_FILE" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_get_paper_file_path_traversal(client, db, tmp_upload_dir):
    """GET /api/papers/:id/file with path outside UPLOAD_DIR -> 404."""
    from app.core.enums import StepName
    from app.papers.models import Paper
    from app.processing.models import PaperStep

    paper_id = uuid.uuid4()
    malicious_path = str(Path(tmp_upload_dir).parent / "etc" / "passwd")

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        file_path=malicious_path,
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    response = await client.get(f"/api/papers/{paper_id}/file")

    assert response.status_code == 404
    assert "NO_FILE" in response.json()["error"]["code"]


# --- Edge cases: delete / update not found ---


@pytest.mark.asyncio
async def test_delete_paper_not_found(client):
    """DELETE /api/papers/:id with unknown id -> 404."""
    fake_id = uuid.uuid4()
    response = await client.delete(f"/api/papers/{fake_id}")

    assert response.status_code == 404
    assert "PAPER_NOT_FOUND" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_update_paper_not_found(client):
    """PATCH /api/papers/:id with unknown id -> 404."""
    fake_id = uuid.uuid4()
    response = await client.patch(
        f"/api/papers/{fake_id}",
        json={"title": "Ghost"},
    )

    assert response.status_code == 404
    assert "PAPER_NOT_FOUND" in response.json()["error"]["code"]
