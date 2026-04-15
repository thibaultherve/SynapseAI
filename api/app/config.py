from pydantic_settings import BaseSettings


class DatabaseConfig(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://synapseai:synapseai@db:5432/synapseai"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class UploadConfig(BaseSettings):
    UPLOAD_DIR: str = "/data/uploads"
    UPLOAD_MAX_SIZE: int = 100 * 1024 * 1024  # 100MB

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class AppConfig(BaseSettings):
    ENV: str = "development"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    ALLOWED_URL_SCHEMES: list[str] = ["http", "https"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class ProcessingConfig(BaseSettings):
    CLAUDE_TIMEOUT: int = 120
    MAX_CONCURRENT_PROCESSING: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class EmbeddingConfig(BaseSettings):
    EMBEDDING_MODEL_NAME: str = "nomic-ai/nomic-embed-text-v1.5"
    EMBEDDING_DIMS: int = 768
    EMBEDDING_CHUNK_SIZE: int = 2048
    EMBEDDING_CHUNK_OVERLAP: int = 200
    EMBEDDING_MAX_WORKERS: int = 2
    EMBEDDING_MAX_CHUNKS_PER_PAPER: int = 200
    EMBEDDING_MAX_TEXT_CHARS: int = 500_000
    EMBEDDING_BATCH_SIZE: int = 32

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class CrossrefConfig(BaseSettings):
    CROSSREF_TOP_K: int = 20
    CROSSREF_COSINE_GATE: float = 0.7
    CROSSREF_MAX_PAIRS_PER_PAPER: int = 10
    CROSSREF_CLAUDE_TIMEOUT: int = 60
    CROSSREF_MAX_DESCRIPTION_LENGTH: int = 500

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class GraphConfig(BaseSettings):
    GRAPH_MAX_NODES: int = 500
    GRAPH_MAX_EDGES: int = 2000
    GRAPH_EGO_MAX_DEPTH: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class InsightConfig(BaseSettings):
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


class ChatConfig(BaseSettings):
    CHAT_CLAUDE_TIMEOUT_PER_CHUNK: float = 30.0
    CHAT_MAX_CONTEXT_CHUNKS: int = 10
    CHAT_MAX_CONTEXT_TOKENS: int = 8000
    CHAT_MAX_MESSAGES_PER_SESSION: int = 100
    CHAT_MAX_HISTORY_MESSAGES: int = 20
    CHAT_MAX_SSE_PER_PAPER: int = 1
    CHAT_MAX_SSE_TOTAL: int = 10
    CHAT_RATE_LIMIT: str = "10/minute"
    CHAT_MAX_MESSAGE_LENGTH: int = 5000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


db_settings = DatabaseConfig()
upload_settings = UploadConfig()
processing_settings = ProcessingConfig()
embedding_settings = EmbeddingConfig()
crossref_settings = CrossrefConfig()
graph_settings = GraphConfig()
insight_settings = InsightConfig()
chat_settings = ChatConfig()
settings = AppConfig()
