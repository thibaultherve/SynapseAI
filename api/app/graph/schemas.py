import uuid
from datetime import date

from pydantic import Field

from app.core.enums import ReferenceStrength, RelationType
from app.core.schemas import AppBaseModel


class NodeResponse(AppBaseModel):
    id: uuid.UUID
    title: str | None = None
    authors_short: str | None = None
    tags: list[str] = []
    degree: int = 0


class EdgeResponse(AppBaseModel):
    source: uuid.UUID
    target: uuid.UUID
    relation_type: RelationType
    strength: ReferenceStrength
    description: str | None = None


class GraphData(AppBaseModel):
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]
    node_count: int
    edge_count: int
    truncated: bool = False


class GraphFilters(AppBaseModel):
    tags: list[int] | None = Field(default=None, max_length=50)
    relation_type: RelationType | None = None
    min_strength: ReferenceStrength | None = None
    date_from: date | None = None
    date_to: date | None = None
