"""Application settings.

Consolidated into a single ``Settings`` class so ``.env`` is parsed once at
import time (vs. once per sub-config previously). Named aliases preserve
the legacy import paths (``db_settings``, ``upload_settings``, ...) — they
all reference the same singleton, so mutating any of them mutates the
shared config.
"""
from __future__ import annotations

import ipaddress

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---- Runtime / root ----
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    ALLOWED_URL_SCHEMES: list[str] = ["http", "https"]
    TRUSTED_PROXIES: list[str] = []

    # ---- Database ----
    DATABASE_URL: str = (
        "postgresql+asyncpg://synapseai:synapseai@db:5432/synapseai"
    )

    # ---- Upload ----
    UPLOAD_DIR: str = "/data/uploads"
    UPLOAD_MAX_SIZE: int = 100 * 1024 * 1024  # 100MB

    # ---- Processing / Claude ----
    CLAUDE_TIMEOUT: int = 120
    MAX_CONCURRENT_PROCESSING: int = 3
    PROCESSING_MAX_SSE_PER_PAPER: int = 3
    PROCESSING_MAX_SSE_TOTAL: int = 50
    PROCESSING_SSE_MAX_DURATION: int = 600
    PROCESSING_SSE_HEARTBEAT_INTERVAL: int = 15

    # ---- Embedding ----
    EMBEDDING_MODEL_NAME: str = "nomic-ai/nomic-embed-text-v1.5"
    EMBEDDING_DIMS: int = 768
    EMBEDDING_CHUNK_SIZE: int = 2048
    EMBEDDING_CHUNK_OVERLAP: int = 200
    EMBEDDING_MAX_WORKERS: int = 2
    EMBEDDING_MAX_CHUNKS_PER_PAPER: int = 200
    EMBEDDING_MAX_TEXT_CHARS: int = 500_000
    EMBEDDING_BATCH_SIZE: int = 32

    # ---- Crossref ----
    CROSSREF_TOP_K: int = 20
    CROSSREF_COSINE_GATE: float = 0.7
    CROSSREF_MAX_PAIRS_PER_PAPER: int = 10
    CROSSREF_CLAUDE_TIMEOUT: int = 60
    CROSSREF_MAX_DESCRIPTION_LENGTH: int = 500

    # ---- Graph ----
    GRAPH_MAX_NODES: int = 500
    GRAPH_MAX_EDGES: int = 2000
    GRAPH_EGO_MAX_DEPTH: int = 3

    # ---- Insights ----
    INSIGHT_DEBOUNCE_SECONDS: int = 30
    INSIGHT_DEDUP_THRESHOLD: float = 0.8
    INSIGHT_MAX_PER_GENERATION: int = 20
    INSIGHT_CLAUDE_TIMEOUT: int = 180
    INSIGHT_CONTEXT_TOP_RATED: int = 20
    INSIGHT_LOOKBACK_HOURS: int = 24
    INSIGHT_MAX_TITLE_LENGTH: int = 300
    INSIGHT_MAX_CONTENT_LENGTH: int = 2000
    INSIGHT_MAX_EVIDENCE_LENGTH: int = 2000
    INSIGHT_CONTEXT_SUMMARY_CHARS: int = 1500
    INSIGHT_MAX_CROSSREFS: int = 200
    INSIGHT_GENERATION_TIMEOUT_MARGIN: int = 30
    INSIGHT_REFRESH_RATE: str = "1/10minute"

    # ---- Chat ----
    CHAT_CLAUDE_TIMEOUT_PER_CHUNK: float = 30.0
    CHAT_MAX_CONTEXT_CHUNKS: int = 10
    CHAT_MAX_CONTEXT_TOKENS: int = 8000
    CHAT_MAX_MESSAGES_PER_SESSION: int = 100
    CHAT_MAX_HISTORY_MESSAGES: int = 20
    CHAT_MAX_SSE_PER_PAPER: int = 1
    CHAT_MAX_SSE_TOTAL: int = 10
    CHAT_RATE_LIMIT: str = "10/minute"
    CHAT_MAX_MESSAGE_LENGTH: int = 5000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _reject_cors_wildcard(cls, value: list[str]) -> list[str]:
        # Browsers reject `Access-Control-Allow-Origin: *` together with
        # `Access-Control-Allow-Credentials: true`; misconfiguring this
        # results in silent CORS failures in prod. Fail fast at boot.
        if any(origin.strip() == "*" for origin in value):
            raise ValueError(
                "CORS_ORIGINS cannot contain '*'; allow_credentials=True is "
                "incompatible with wildcard origins"
            )
        return value

    @field_validator("TRUSTED_PROXIES")
    @classmethod
    def _validate_trusted_proxies(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            candidate = raw.strip()
            if not candidate:
                continue
            try:
                ipaddress.ip_network(candidate, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"TRUSTED_PROXIES entry {candidate!r} is not a valid IP or CIDR"
                ) from exc
            cleaned.append(candidate)
        return cleaned


settings: Settings = Settings()

# Legacy aliases — every sub-config now points at the same singleton.
# Keeping them means existing imports and tests that monkeypatch
# ``app.config.upload_settings.UPLOAD_DIR`` etc. keep working unchanged.
db_settings: Settings = settings
upload_settings: Settings = settings
processing_settings: Settings = settings
embedding_settings: Settings = settings
crossref_settings: Settings = settings
graph_settings: Settings = settings
insight_settings: Settings = settings
chat_settings: Settings = settings


__all__: tuple[str, ...] = (
    "Settings",
    "settings",
    "db_settings",
    "upload_settings",
    "processing_settings",
    "embedding_settings",
    "crossref_settings",
    "graph_settings",
    "insight_settings",
    "chat_settings",
)
