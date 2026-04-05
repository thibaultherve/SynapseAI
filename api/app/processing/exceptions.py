from app.core.exceptions import AppError


class ClaudeError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=502)
