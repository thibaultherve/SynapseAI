from app.core.exceptions import ConflictError, NotFoundError
from app.tags.constants import ErrorCode


class TagNotFoundError(NotFoundError):
    def __init__(self, tag_id: int):
        super().__init__(ErrorCode.TAG_NOT_FOUND, f"Tag {tag_id} not found")


class DuplicateTagError(ConflictError):
    def __init__(self):
        super().__init__(
            ErrorCode.DUPLICATE_TAG,
            "Tag with this name and category already exists",
        )
