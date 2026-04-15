from app.core.exceptions import ConflictError, NotFoundError
from app.insights.constants import ErrorCode


class InsightNotFoundError(NotFoundError):
    def __init__(self, insight_id: int):
        super().__init__(
            ErrorCode.INSIGHT_NOT_FOUND,
            f"Insight {insight_id} not found",
        )


class InsightRefreshBusyError(ConflictError):
    def __init__(self):
        super().__init__(
            ErrorCode.INSIGHT_REFRESH_BUSY,
            "Insight refresh already in progress",
        )
