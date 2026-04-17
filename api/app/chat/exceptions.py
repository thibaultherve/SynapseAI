import uuid

from app.chat.constants import ErrorCode
from app.core.exceptions import AppError, ConflictError, NotFoundError


class SessionNotFoundError(NotFoundError):
    def __init__(self, session_id: uuid.UUID):
        super().__init__(
            ErrorCode.SESSION_NOT_FOUND, f"Chat session {session_id} not found"
        )


class SessionScopeMismatchError(ConflictError):
    def __init__(self):
        super().__init__(
            ErrorCode.SESSION_SCOPE_MISMATCH,
            "Session scope or paper does not match the request",
        )


class SessionFullError(ConflictError):
    def __init__(self, limit: int):
        super().__init__(
            ErrorCode.SESSION_FULL,
            f"Chat session has reached the maximum of {limit} messages",
        )


class ChatCapacityError(AppError):
    def __init__(self):
        super().__init__(
            ErrorCode.CHAT_CAPACITY,
            "Server at chat SSE capacity",
            status_code=503,
        )


class ChatBusyError(AppError):
    def __init__(self):
        super().__init__(
            ErrorCode.CHAT_BUSY,
            "Another chat stream is already active for this paper",
            status_code=429,
        )
