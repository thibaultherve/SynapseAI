import uuid
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import SourceType, StepName
from app.papers.models import PaperTag
from app.tags.models import Tag

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
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert str(paper_id) in disposition
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
    assert response.json()["error"]["code"] == "PAPER_FILE_MISSING"


@pytest.mark.asyncio
async def test_get_paper_file_path_traversal(client, db, tmp_upload_dir):
    """GET /api/papers/:id/file with path outside UPLOAD_DIR -> 404."""
    from app.core.enums import StepName
    from app.papers.models import Paper
    from app.processing.models import PaperStep

    paper_id = uuid.uuid4()
    outside_dir = Path(tmp_upload_dir).parent / "outside"
    outside_dir.mkdir(exist_ok=True)
    malicious_path = outside_dir / "secret.pdf"
    malicious_path.write_bytes(b"%PDF-1.4 outside")

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        file_path=str(malicious_path),
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    response = await client.get(f"/api/papers/{paper_id}/file")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PAPER_FILE_MISSING"


@pytest.mark.asyncio
async def test_get_paper_file_symlink_rejected(client, db, tmp_upload_dir):
    """GET /api/papers/:id/file when file_path is a symlink -> 404 opaque.

    A symlink planted inside UPLOAD_DIR pointing at an arbitrary file would
    pass `is_relative_to(upload_dir)` after `resolve()`, so the endpoint must
    reject symlinks at the leaf explicitly.
    """
    from app.core.enums import StepName
    from app.papers.models import Paper
    from app.processing.models import PaperStep

    paper_id = uuid.uuid4()
    target = Path(tmp_upload_dir) / f"{paper_id}-target.pdf"
    target.write_bytes(b"%PDF-1.4 target")
    link_path = Path(tmp_upload_dir) / f"{paper_id}.pdf"
    try:
        link_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported on this platform")

    paper = Paper(
        id=paper_id,
        source_type=SourceType.PDF,
        file_path=str(link_path),
    )
    db.add(paper)
    await db.flush()
    for step_name in StepName:
        db.add(PaperStep(paper_id=paper_id, step=step_name.value))
    await db.commit()

    response = await client.get(f"/api/papers/{paper_id}/file")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PAPER_FILE_MISSING"


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


# --- Phase 5c: Advanced Filters ---


@pytest.mark.asyncio
async def test_filter_by_tag_single_and_multi(client, paper_factory, db):
    """T17: Filter papers by tag IDs (OR logic)."""
    p1 = await paper_factory(title="Paper A")
    p2 = await paper_factory(title="Paper B")
    await paper_factory(title="Paper C")  # no tags

    tag1 = Tag(name="neuroscience", category="sub_domain")
    tag2 = Tag(name="fMRI", category="technique")
    db.add_all([tag1, tag2])
    await db.flush()

    db.add(PaperTag(paper_id=p1.id, tag_id=tag1.id))
    db.add(PaperTag(paper_id=p2.id, tag_id=tag2.id))
    await db.commit()

    # Single tag
    resp = await client.get(f"/api/papers?tags={tag1.id}")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert str(p1.id) in ids
    assert str(p2.id) not in ids

    # Multi tags (OR)
    resp = await client.get(f"/api/papers?tags={tag1.id}&tags={tag2.id}")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert str(p1.id) in ids
    assert str(p2.id) in ids


@pytest.mark.asyncio
async def test_filter_by_state(client, paper_factory):
    """T18: Filter papers by derived state (readable, error)."""
    # readable = summarizing done, but not all non-crossref done
    await paper_factory(
        title="Readable",
        steps={
            "uploading": "done",
            "extracting": "done",
            "summarizing": "done",
            "tagging": "pending",
            "embedding": "pending",
            "crossrefing": "pending",
        },
    )
    # error
    await paper_factory(
        title="Errored",
        steps={
            "uploading": "done",
            "extracting": "error",
            "summarizing": "pending",
            "tagging": "pending",
            "embedding": "pending",
            "crossrefing": "pending",
        },
    )
    # pending
    await paper_factory(title="Pending")

    resp = await client.get("/api/papers?state=readable")
    assert resp.status_code == 200
    titles = [p["title"] for p in resp.json()]
    assert "Readable" in titles
    assert "Errored" not in titles
    assert "Pending" not in titles

    resp = await client.get("/api/papers?state=error")
    titles = [p["title"] for p in resp.json()]
    assert "Errored" in titles
    assert "Readable" not in titles


@pytest.mark.asyncio
async def test_filter_by_date_range(client, paper_factory):
    """T19: Filter papers by publication date range."""
    await paper_factory(title="Old", publication_date=date(2023, 1, 15))
    await paper_factory(title="Mid", publication_date=date(2024, 6, 1))
    await paper_factory(title="New", publication_date=date(2025, 3, 10))
    await paper_factory(title="No date")  # null publication_date

    resp = await client.get("/api/papers?date_from=2024-01-01&date_to=2024-12-31")
    assert resp.status_code == 200
    titles = [p["title"] for p in resp.json()]
    assert "Mid" in titles
    assert "Old" not in titles
    assert "New" not in titles
    assert "No date" not in titles


@pytest.mark.asyncio
async def test_filter_fts(client, paper_factory):
    """T20: Full-text search via q param (websearch_to_tsquery)."""
    await paper_factory(
        title="Dopamine Reward Circuits",
        short_summary="A study on dopamine pathways in the brain.",
    )
    await paper_factory(
        title="Protein Folding Mechanisms",
        short_summary="Analysis of protein structure prediction.",
    )

    resp = await client.get("/api/papers?q=dopamine+reward")
    assert resp.status_code == 200
    titles = [p["title"] for p in resp.json()]
    assert "Dopamine Reward Circuits" in titles
    assert "Protein Folding Mechanisms" not in titles


@pytest.mark.asyncio
async def test_filter_combination(client, paper_factory, db):
    """T21: Combine multiple filters together."""
    p1 = await paper_factory(
        title="Target Paper",
        publication_date=date(2024, 5, 1),
        short_summary="Oligodendrocyte precursor cells study.",
        steps={
            "uploading": "done",
            "extracting": "done",
            "summarizing": "done",
            "tagging": "pending",
            "embedding": "pending",
            "crossrefing": "pending",
        },
    )
    await paper_factory(
        title="Wrong State",
        publication_date=date(2024, 5, 1),
        short_summary="Oligodendrocyte analysis.",
    )  # pending state

    tag = Tag(name="neuroscience", category="sub_domain")
    db.add(tag)
    await db.flush()
    db.add(PaperTag(paper_id=p1.id, tag_id=tag.id))
    await db.commit()

    resp = await client.get(
        f"/api/papers?tags={tag.id}&state=readable&date_from=2024-01-01&q=oligodendrocyte"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Target Paper"
