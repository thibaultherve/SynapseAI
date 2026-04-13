"""Upgrade paper_embedding: Vector(384) -> Vector(768) + HNSW index rebuild

Revision ID: 004b
Revises: 004a
Create Date: 2026-04-13

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004b"
down_revision: str = "004a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No existing embeddings in Sprint 2 — safe to truncate
    op.execute("TRUNCATE paper_embedding")

    # Change vector dimension from 384 to 768
    op.execute("ALTER TABLE paper_embedding DROP COLUMN embedding")
    op.execute("ALTER TABLE paper_embedding ADD COLUMN embedding vector(768)")

    # Rebuild HNSW index with optimal params for 768 dims
    op.execute("DROP INDEX IF EXISTS idx_embeddings_vec")
    op.execute(
        "CREATE INDEX idx_embeddings_vec ON paper_embedding "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 200)"
    )

    # Additional indexes for search performance
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_embedding_paper "
        "ON paper_embedding (paper_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embedding_paper")
    op.execute("DROP INDEX IF EXISTS idx_embeddings_vec")
    op.execute("TRUNCATE paper_embedding")
    op.execute("ALTER TABLE paper_embedding DROP COLUMN embedding")
    op.execute("ALTER TABLE paper_embedding ADD COLUMN embedding vector(384)")
    op.execute(
        "CREATE INDEX idx_embeddings_vec ON paper_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
