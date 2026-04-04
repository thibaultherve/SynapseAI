import asyncio
import os
import sys

# Ensure the api root (/app) is on the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.base import Base

# Import all models so Alembic detects them
from app.papers.models import Paper, PaperTag  # noqa: F401
from app.tags.models import Tag  # noqa: F401
from app.processing.models import CrossReference, PaperEmbedding, ProcessingEvent  # noqa: F401
from app.insights.models import Insight, InsightPaper  # noqa: F401
from app.chat.models import ChatMessage, ChatSession  # noqa: F401

config = context.config

target_metadata = Base.metadata

# Override sqlalchemy.url from env var
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def include_object(object, name, type_, reflected, compare_to):
    """Filter out Computed columns and Vector types from autogenerate diffs."""
    if type_ == "column" and hasattr(object, "computed") and object.computed is not None:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
