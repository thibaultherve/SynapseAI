"""T23: Chunking — sentence boundaries, max chunks, overlap."""

import pytest

from app.utils.chunking import chunk_text


class TestChunkText:
    def test_empty_text_returns_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []
        assert chunk_text(None) == []

    def test_short_text_single_chunk(self):
        text = "This is a short sentence."
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_sentence_boundary_splitting(self):
        text = "First sentence. Second sentence. Third sentence."
        # With a very small chunk_size, each sentence becomes its own chunk
        chunks = chunk_text(text, chunk_size=30, overlap=0)
        assert len(chunks) >= 2
        # Each chunk should end at a sentence boundary
        for chunk in chunks:
            assert chunk.strip()

    def test_paragraph_boundary_respected(self):
        text = "Paragraph one sentence.\n\nParagraph two sentence."
        chunks = chunk_text(text, chunk_size=50, overlap=0)
        assert len(chunks) >= 1
        # Both paragraphs' sentences should appear
        full = " ".join(chunks)
        assert "Paragraph one" in full
        assert "Paragraph two" in full

    def test_max_chunks_enforced(self):
        # Create a long text with many sentences
        text = " ".join([f"Sentence number {i}." for i in range(500)])
        chunks = chunk_text(text, chunk_size=50, overlap=0, max_chunks=5)
        assert len(chunks) <= 5

    def test_overlap_between_chunks(self):
        # Create text with distinct sentences
        sentences = [f"Sentence {i} content here." for i in range(20)]
        text = " ".join(sentences)
        chunks = chunk_text(text, chunk_size=100, overlap=30)

        # With overlap, consecutive chunks should share some content
        if len(chunks) >= 2:
            # The end of chunk N should overlap with the start of chunk N+1
            # At minimum, there should be some shared words
            for i in range(len(chunks) - 1):
                words_current = set(chunks[i].split())
                words_next = set(chunks[i + 1].split())
                shared = words_current & words_next
                assert len(shared) > 0, (
                    f"Chunks {i} and {i+1} should overlap but share no words"
                )

    def test_max_text_chars_truncation(self):
        text = "A" * 10_000
        chunks = chunk_text(text, max_text_chars=1000)
        total_chars = sum(len(c) for c in chunks)
        # Should not process more than max_text_chars
        assert total_chars <= 1100  # some overhead from overlap

    def test_long_sentence_not_dropped(self):
        # A single sentence longer than chunk_size should still produce a chunk
        long = "This is a very long sentence " * 100
        chunks = chunk_text(long.strip() + ".", chunk_size=50, overlap=0)
        assert len(chunks) >= 1

    def test_real_world_text(self):
        text = (
            "Oligodendrocyte precursor cells (OPCs) are distributed throughout the "
            "central nervous system. They differentiate into mature oligodendrocytes "
            "that produce myelin sheaths. Recent single-cell RNA sequencing studies "
            "have revealed heterogeneous OPC populations.\n\n"
            "In this study, we performed scRNA-seq analysis on mouse brain samples "
            "at multiple developmental stages. We identified novel OPC subtypes with "
            "distinct transcriptional signatures. These findings suggest that OPC "
            "diversity is greater than previously appreciated."
        )
        chunks = chunk_text(text)
        assert len(chunks) >= 1
        # All text should be represented
        full = " ".join(chunks)
        assert "Oligodendrocyte" in full
        assert "scRNA-seq" in full
