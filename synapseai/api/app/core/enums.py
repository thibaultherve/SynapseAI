from enum import StrEnum


class PaperStatus(StrEnum):
    UPLOADING = "uploading"
    EXTRACTING = "extracting"
    SUMMARIZING = "summarizing"
    SUMMARIZED = "summarized"
    TAGGING = "tagging"
    EMBEDDING = "embedding"
    CROSSREFING = "crossrefing"
    DONE = "done"
    ERROR = "error"
    DELETED = "deleted"


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
