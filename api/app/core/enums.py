from enum import StrEnum


class StepName(StrEnum):
    UPLOADING = "uploading"
    EXTRACTING = "extracting"
    SUMMARIZING = "summarizing"
    TAGGING = "tagging"
    EMBEDDING = "embedding"
    CROSSREFING = "crossrefing"


class StepStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


class DerivedPaperStatus(StrEnum):
    """Computed paper status derived from step statuses."""

    PENDING = "pending"
    PROCESSING = "processing"
    READABLE = "readable"
    ENRICHED = "enriched"
    ERROR = "error"


class SourceType(StrEnum):
    PDF = "pdf"
    WEB = "web"


class TagCategory(StrEnum):
    SUB_DOMAIN = "sub_domain"
    TECHNIQUE = "technique"
    PATHOLOGY = "pathology"
    TOPIC = "topic"


class RelationType(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    EXTENDS = "extends"
    METHODOLOGICAL = "methodological"
    THEMATIC = "thematic"


class ReferenceStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class InsightType(StrEnum):
    TREND = "trend"
    GAP = "gap"
    HYPOTHESIS = "hypothesis"
    METHODOLOGY = "methodology"
    CONTRADICTION = "contradiction"
    OPPORTUNITY = "opportunity"


class InsightConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ChatScope(StrEnum):
    PAPER = "paper"
    CORPUS = "corpus"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
