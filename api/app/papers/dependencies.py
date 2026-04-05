import uuid

from fastapi import Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import upload_settings
from app.core.database import get_db
from app.core.exceptions import ValidationError
from app.papers.constants import ErrorCode
from app.papers.exceptions import PaperNotFoundError, UploadTooLargeError
from app.papers.models import Paper


async def get_paper_or_404(
    paper_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Paper:
    paper = await db.get(Paper, paper_id)
    if not paper:
        raise PaperNotFoundError(str(paper_id))
    return paper


async def validate_upload(file: UploadFile = File(...)) -> bytes:
    """Validate PDF upload: magic bytes + size limit. Returns file content."""
    header = await file.read(5)
    await file.seek(0)
    if not header.startswith(b"%PDF"):
        raise ValidationError(ErrorCode.INVALID_FILE_TYPE, "Only PDF files are accepted")

    chunks = []
    total = 0
    while chunk := await file.read(8192):
        total += len(chunk)
        if total > upload_settings.UPLOAD_MAX_SIZE:
            raise UploadTooLargeError()
        chunks.append(chunk)

    return b"".join(chunks)
