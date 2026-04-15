"""Sentence-boundary-aware text chunking for embedding generation."""

import re

from app.config import embedding_settings


def chunk_text(
    text: str,
    chunk_size: int = embedding_settings.EMBEDDING_CHUNK_SIZE,
    overlap: int = embedding_settings.EMBEDDING_CHUNK_OVERLAP,
    max_chunks: int = embedding_settings.EMBEDDING_MAX_CHUNKS_PER_PAPER,
    max_text_chars: int = embedding_settings.EMBEDDING_MAX_TEXT_CHARS,
) -> list[str]:
    """Split text into overlapping chunks respecting sentence boundaries.

    Strategy:
    1. Truncate to max_text_chars
    2. Split into paragraphs, then sentences
    3. Build chunks up to chunk_size chars, breaking at sentence boundaries
    4. Overlap by re-including trailing sentences from previous chunk
    5. Cap at max_chunks
    """
    if not text or not text.strip():
        return []

    text = text[:max_text_chars]

    sentences = _split_sentences(text)
    if not sentences:
        return []

    chunks: list[str] = []
    current_start = 0

    while current_start < len(sentences) and len(chunks) < max_chunks:
        chunk_chars = 0
        end = current_start

        # Build chunk up to chunk_size
        while end < len(sentences) and chunk_chars + len(sentences[end]) <= chunk_size:
            chunk_chars += len(sentences[end])
            end += 1

        # Ensure at least one sentence per chunk
        if end == current_start:
            end = current_start + 1

        chunk = " ".join(sentences[current_start:end])
        if chunk.strip():
            chunks.append(chunk.strip())

        # Advance with overlap: step back enough sentences to cover overlap chars
        if end >= len(sentences):
            break

        overlap_chars = 0
        overlap_start = end
        while overlap_start > current_start and overlap_chars < overlap:
            overlap_start -= 1
            overlap_chars += len(sentences[overlap_start])

        current_start = overlap_start if overlap_start > current_start else end

    return chunks[:max_chunks]


# Sentence boundary regex: split on .!? followed by whitespace,
# with a simple lookbehind for at least 2 chars before the punctuation
# (avoids splitting on abbreviations like "Dr." or "al.")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving paragraph structure."""
    paragraphs = _PARAGRAPH_SPLIT.split(text.strip())
    sentences: list[str] = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        parts = _SENTENCE_SPLIT.split(para)
        for part in parts:
            part = part.strip()
            if part:
                sentences.append(part)

    return sentences
