import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core.schemas import AppBaseModel

TAG_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9\s\-/().,'+]+$")
TAG_CATEGORIES = ("sub_domain", "technique", "pathology", "topic")


class TagCreate(AppBaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    category: Literal["sub_domain", "technique", "pathology", "topic"]
    description: str | None = Field(None, max_length=500)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not TAG_NAME_PATTERN.match(v):
            raise ValueError(
                "Tag name may only contain letters, digits, spaces, "
                "and the characters: - / ( ) . , ' +"
            )
        return v


class TagUpdate(AppBaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None and not TAG_NAME_PATTERN.match(v):
            raise ValueError(
                "Tag name may only contain letters, digits, spaces, "
                "and the characters: - / ( ) . , ' +"
            )
        return v


class TagMergeRequest(AppBaseModel):
    source_id: int
    target_id: int

    @model_validator(mode="after")
    def source_not_target(self) -> "TagMergeRequest":
        if self.source_id == self.target_id:
            raise ValueError("source_id and target_id must be different")
        return self


class TagResponse(AppBaseModel):
    id: int
    name: str
    category: str
    description: str | None = None
    created_at: datetime


class TagWithCountResponse(AppBaseModel):
    id: int
    name: str
    category: str
    description: str | None = None
    created_at: datetime
    paper_count: int = 0
