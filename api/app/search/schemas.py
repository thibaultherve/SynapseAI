import uuid
from datetime import date
from enum import StrEnum

from pydantic import Field

from app.core.schemas import AppBaseModel


class SearchMode(StrEnum):
    EXACT = "exact"
    SEMANTIC = "semantic"


class SearchFilters(AppBaseModel):
    tags: list[int] | None = Field(None, max_length=50)
    date_from: date | None = None
    date_to: date | None = None


class SearchRequest(AppBaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    mode: SearchMode = SearchMode.EXACT
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)
    filters: SearchFilters | None = None


class SearchResultItem(AppBaseModel):
    id: uuid.UUID
    title: str | None = None
    authors_short: str | None = None
    journal: str | None = None
    doi: str | None = None
    short_summary: str | None = None
    keywords: list[str] | None = None
    snippet: str | None = None
    relevance_score: float
    tags: list[str] = Field(default_factory=list)


class SearchResponse(AppBaseModel):
    results: list[SearchResultItem]
    total_count: int
    query: str
    mode: SearchMode
