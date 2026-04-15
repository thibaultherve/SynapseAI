from datetime import date

from fastapi import Query

from app.config import graph_settings
from app.core.enums import ReferenceStrength, RelationType
from app.graph.schemas import GraphFilters


async def get_graph_filters(
    tags: list[int] | None = Query(
        None, max_length=50, description="Tag IDs (OR logic)"
    ),
    relation_type: RelationType | None = Query(None),
    min_strength: ReferenceStrength | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
) -> GraphFilters:
    return GraphFilters(
        tags=tags,
        relation_type=relation_type,
        min_strength=min_strength,
        date_from=date_from,
        date_to=date_to,
    )


async def get_ego_depth(
    depth: int = Query(
        1,
        ge=1,
        le=graph_settings.GRAPH_EGO_MAX_DEPTH,
        description="BFS depth",
    ),
) -> int:
    return depth
