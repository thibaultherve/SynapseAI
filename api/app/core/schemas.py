from pydantic import BaseModel, ConfigDict


class AppBaseModel(BaseModel):
    """Base model shared by all schemas."""

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )


class HealthResponse(AppBaseModel):
    status: str
    database: str


class ErrorDetail(AppBaseModel):
    code: str
    message: str


class ErrorResponse(AppBaseModel):
    error: ErrorDetail
