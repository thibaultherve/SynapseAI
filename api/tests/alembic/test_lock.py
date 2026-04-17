"""Verify the Alembic advisory-lock helper acquires and releases correctly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.sql.elements import TextClause

from app.core.migration_lock import (
    MIGRATION_ADVISORY_LOCK_KEY,
    migration_advisory_lock,
)


def _sql_and_params(call) -> tuple[str, dict]:
    arg = call.args[0]
    assert isinstance(arg, TextClause)
    compiled = arg.compile()
    return str(compiled), dict(compiled.params)


def test_migration_advisory_lock_acquires_then_releases():
    connection = MagicMock(name="connection")

    with migration_advisory_lock(connection):
        # Inside the context manager: lock must already be acquired
        assert connection.execute.call_count == 1
        lock_sql, lock_params = _sql_and_params(connection.execute.call_args_list[0])
        assert "pg_advisory_lock" in lock_sql
        assert lock_params == {"key": MIGRATION_ADVISORY_LOCK_KEY}

    # After the block: unlock must have been issued
    assert connection.execute.call_count == 2
    unlock_sql, unlock_params = _sql_and_params(connection.execute.call_args_list[1])
    assert "pg_advisory_unlock" in unlock_sql
    assert unlock_params == {"key": MIGRATION_ADVISORY_LOCK_KEY}


def test_migration_advisory_lock_releases_on_failure():
    """If the migration body raises, the unlock must still fire."""
    connection = MagicMock(name="connection")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with migration_advisory_lock(connection):
            raise Boom("migration failure")

    assert connection.execute.call_count == 2
    unlock_sql, _ = _sql_and_params(connection.execute.call_args_list[1])
    assert "pg_advisory_unlock" in unlock_sql
