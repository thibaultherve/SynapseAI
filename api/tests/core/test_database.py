import pytest
from sqlalchemy import text

from tests.conftest import test_session


@pytest.mark.asyncio
async def test_database_connection():
    async with test_session() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
