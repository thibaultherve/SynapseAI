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
chat_settings = ChatConfig()
settings = AppConfig()
