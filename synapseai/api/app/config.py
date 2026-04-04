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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


db_settings = DatabaseConfig()
upload_settings = UploadConfig()
settings = AppConfig()
