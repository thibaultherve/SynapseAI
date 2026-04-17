from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.papers.constants import ErrorCode


class PaperNotFoundError(NotFoundError):
    def __init__(self, paper_id: str):
        super().__init__(ErrorCode.PAPER_NOT_FOUND, f"Paper {paper_id} not found")


class PaperFileMissingError(NotFoundError):
    def __init__(self, message: str = "Paper file unavailable"):
        super().__init__(ErrorCode.PAPER_FILE_MISSING, message)


class InvalidDOIError(ValidationError):
    def __init__(self, message: str = "Invalid DOI format. Expected: 10.XXXX/..."):
        super().__init__(ErrorCode.INVALID_DOI, message)


class UploadTooLargeError(AppError):
    def __init__(self, message: str = "File exceeds 100MB limit"):
        super().__init__(ErrorCode.FILE_TOO_LARGE, message, status_code=413)
