class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 500):
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=404)


class ConflictError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=409)


class ValidationError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=422)
