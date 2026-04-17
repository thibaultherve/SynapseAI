"""F30 spot-check: Paper.updated_at fires onupdate=func.now() on mutation.

func.now() maps to Postgres transaction_timestamp(), which is stable within
a single transaction — so we MUST commit between INSERT and UPDATE to observe
the timestamp advancing.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.enums import SourceType
from app.papers.models import Paper


@pytest.mark.asyncio
async def test_updated_at_advances_on_mutation(db):
    paper = Paper(
        id=uuid.uuid4(),
        source_type=SourceType.PDF,
        title="Original title",
    )
    db.add(paper)
    await db.commit()
    await db.refresh(paper)

    created_at = paper.created_at
    initial_updated_at = paper.updated_at
    assert created_at is not None
    assert initial_updated_at is not None
    # INSERT path: both come from server_default=func.now() in the same
    # transaction — they are equal, not ordered.
    assert initial_updated_at == created_at

    # Sleep clears transaction_timestamp granularity between commits.
    await asyncio.sleep(0.05)

    paper.title = "Mutated title"
    await db.commit()
    await db.refresh(paper)

    assert paper.updated_at > initial_updated_at
    assert paper.created_at == created_at
