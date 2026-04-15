"""T34: Chat context builders (paper mode + corpus mode)."""

import pytest

from app.chat import service
from app.core.enums import SourceType


@pytest.mark.asyncio
async def test_build_paper_context_includes_summary_and_chunks(
    db, paper_factory, embedding_factory, mock_embedding
):
    """Paper-mode context contains short_summary, key_findings, and top-K chunks."""
    paper = await paper_factory(
        source_type=SourceType.PDF,
        title="Oligodendrocyte study",
        short_summary="Short summary about oligodendrocytes.",
        key_findings="1. Finding A.\n2. Finding B.",
    )
    await embedding_factory(
        paper.id,
        chunks=["Chunk one text", "Chunk two text", "Chunk three text"],
    )

    context = await service.build_paper_context(db, paper, "What are the findings?")

    assert "Short summary about oligodendrocytes." in context
    assert "Finding A" in context
    assert "<chunk" in context


@pytest.mark.asyncio
async def test_build_corpus_context_pulls_cross_paper_chunks(
    db, paper_factory, embedding_factory, mock_embedding
):
    """Corpus-mode context includes chunks from multiple papers."""
    paper_a = await paper_factory(
        source_type=SourceType.PDF, title="Paper A"
    )
    paper_b = await paper_factory(
        source_type=SourceType.PDF, title="Paper B"
    )
    await embedding_factory(paper_a.id, chunks=["A chunk one"])
    await embedding_factory(paper_b.id, chunks=["B chunk one"])

    context = await service.build_corpus_context(db, "anything")

    assert "A chunk one" in context or "B chunk one" in context
    assert "<chunk" in context


@pytest.mark.asyncio
async def test_build_corpus_context_empty_corpus_returns_placeholder(
    db, mock_embedding
):
    """Corpus-mode with no embeddings returns a placeholder message."""
    context = await service.build_corpus_context(db, "anything")
    assert "No relevant chunks" in context
