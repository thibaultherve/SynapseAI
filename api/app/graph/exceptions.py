from app.core.exceptions import AppError
from app.graph.constants import ErrorCode

_DEFAULT_SUGGESTION = (
    "use /api/graph/paper/:id or apply filters "
    "(tags, relation_type, min_strength, date_from, date_to)"
)


class GraphTooLargeError(AppError):
    def __init__(
        self,
        node_count: int,
        edge_count: int,
        *,
        suggestion: str = _DEFAULT_SUGGESTION,
    ):
        message = (
            f"Graph too large: {node_count} nodes, {edge_count} edges exceed clamps. "
            f"{suggestion}"
        )
        super().__init__(ErrorCode.GRAPH_TOO_LARGE, message, status_code=413)
