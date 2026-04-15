from enum import StrEnum


class ErrorCode(StrEnum):
    INSIGHT_NOT_FOUND = "INSIGHT_NOT_FOUND"
    INSIGHT_REFRESH_BUSY = "INSIGHT_REFRESH_BUSY"
