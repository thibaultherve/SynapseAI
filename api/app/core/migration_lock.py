"""Advisory-lock helpers for Alembic migrations.

Lives under ``app.core`` (importable) rather than ``alembic/`` (not a package)
so tests can exercise it without loading ``alembic/env.py`` top-level.
"""

from contextlib import contextmanager

from sqlalchemy import text

# Fixed 64-bit key used by every deploy — two processes racing on the same
# database block each other instead of corrupting the Alembic history.
MIGRATION_ADVISORY_LOCK_KEY = 1234567890


@contextmanager
def migration_advisory_lock(connection):
    """Acquire/release ``pg_advisory_lock`` around an Alembic migration run."""
    connection.execute(
        text("SELECT pg_advisory_lock(:key)").bindparams(
            key=MIGRATION_ADVISORY_LOCK_KEY
        )
    )
    try:
        yield
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:key)").bindparams(
                key=MIGRATION_ADVISORY_LOCK_KEY
            )
        )
