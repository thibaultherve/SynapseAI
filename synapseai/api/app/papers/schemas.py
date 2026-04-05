import re
import uuid
from datetime import date, datetime

from pydantic import Field, HttpUrl, field_validator, model_validator

from app.core.enums import PaperStatus, SourceType
from app.core.schemas import AppBaseModel

DOI_PATTERN = r"^10\.\d{4,}/\S+$"


def _validate_doi(v: str | None) -> str | None:
    if v is not None and not re.match(DOI_PATTERN, v):
        raise ValueError("Invalid DOI format. Expected: 10.XXXX/...")
    return v


class PaperCreate(AppBaseModel):
    url: HttpUrl | None = None
    doi: str | None = None

    @model_validator(mode="after")
    def require_url_or_doi(self) -> "PaperCreate":
        if not self.url and not self.doi:
            raise ValueError("Either 'url' or 'doi' must be provided")
        if self.url and self.doi:
            raise ValueError("Provide either 'url' or 'doi', not both")
        return self

    @field_validator("doi")
    @classmethod
    def validate_doi_format(cls, v: str | None) -> str | None:
        return _validate_doi(v)


class PaperUpdate(AppBaseModel):
    title: str | None = Field(None, max_length=500)
    authors: list[str] | None = Field(None, max_length=50)
    authors_short: str | None = Field(None, max_length=200)
    publication_date: date | None = None
    journal: str | None = Field(None, max_length=300)
    doi: str | None = None
    url: HttpUrl | None = None

    @field_validator("doi")
    @classmethod
    def validate_doi_format(cls, v: str | None) -> str | None:
        return _validate_doi(v)


class PaperResponse(AppBaseModel):
    id: uuid.UUID
    title: str | None = None
    authors: list[str] | None = None
    authors_short: str | None = None
    publication_date: date | None = None
    journal: str | None = None
    doi: str | None = None
    url: str | None = None
    source_type: SourceType | None = None
    status: PaperStatus
    error_message: str | None = None
    extracted_text: str | None = None
    short_summary: str | None = None
    detailed_summary: str | None = None
    key_findings: str | None = None
    keywords: list[str] | None = None
    word_count: int | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None


class PaperSummaryResponse(AppBaseModel):
    id: uuid.UUID
    title: str | None = None
    authors_short: str | None = None
    journal: str | None = None
    doi: str | None = None
    source_type: SourceType | None = None
    status: PaperStatus
    short_summary: str | None = None
    keywords: list[str] | None = None
    word_count: int | None = None
    created_at: datetime
    updated_at: datetime
