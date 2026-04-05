"""Initial schema — 10 tables + pgvector

Revision ID: 001
Revises:
Create Date: 2026-04-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 1. tag
    op.create_table(
        "tag",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", "category", name="uq_tag_name_category"),
        sa.CheckConstraint(
            "category IN ('sub_domain', 'technique', 'pathology', 'topic')",
            name="ck_tag_valid_category",
        ),
    )

    # 2. paper
    op.create_table(
        "paper",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text),
        sa.Column("authors", ARRAY(sa.Text)),
        sa.Column("authors_short", sa.Text),
        sa.Column("publication_date", sa.Date),
        sa.Column("journal", sa.Text),
        sa.Column("doi", sa.Text, unique=True),
        sa.Column("url", sa.Text),
        sa.Column("source_type", sa.String(10)),
        sa.Column("status", sa.String(20), server_default="uploading", nullable=False),
        sa.Column("error_message", sa.Text),
        sa.Column("extracted_text", sa.Text),
        sa.Column("short_summary", sa.Text),
        sa.Column("detailed_summary", sa.Text),
        sa.Column("key_findings", sa.Text),
        sa.Column("keywords", ARRAY(sa.Text)),
        sa.Column("word_count", sa.Integer),
        sa.Column("file_path", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "search_vector",
            TSVECTOR,
            sa.Computed(
                "to_tsvector('english', "
                "coalesce(title, '') || ' ' || "
                "coalesce(short_summary, '') || ' ' || "
                "coalesce(extracted_text, ''))",
                persisted=True,
            ),
        ),
        sa.CheckConstraint(
            "source_type IN ('pdf', 'web')",
            name="ck_paper_valid_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('uploading', 'extracting', 'summarizing', 'tagging', "
            "'embedding', 'crossrefing', 'done', 'error', 'deleted')",
            name="ck_paper_valid_status",
        ),
    )

    # 3. paper_tag
    op.create_table(
        "paper_tag",
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
    )

    # 4. paper_embedding
    op.create_table(
        "paper_embedding",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chunk_text", sa.Text),
        sa.UniqueConstraint("paper_id", "chunk_index", name="uq_paper_embedding_paper_chunk"),
    )
    # Add vector column via raw SQL (pgvector type not natively supported by Alembic ops)
    op.execute("ALTER TABLE paper_embedding ADD COLUMN embedding vector(384)")

    # 5. cross_reference
    op.create_table(
        "cross_reference",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("paper_a", sa.Uuid, sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_b", sa.Uuid, sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", sa.String(20), nullable=False),
        sa.Column("strength", sa.String(10), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("paper_a", "paper_b", name="uq_cross_reference_pair"),
        sa.CheckConstraint("paper_a < paper_b", name="ck_cross_reference_ordered_pair"),
        sa.CheckConstraint(
            "relation_type IN ('supports', 'contradicts', 'extends', 'methodological', 'thematic')",
            name="ck_cross_reference_valid_relation_type",
        ),
        sa.CheckConstraint(
            "strength IN ('strong', 'moderate', 'weak')",
            name="ck_cross_reference_valid_strength",
        ),
    )

    # 6. insight
    op.create_table(
        "insight",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("evidence", sa.Text),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("rating", sa.SmallInteger, nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "type IN ('trend', 'gap', 'hypothesis', 'methodology', 'contradiction', 'opportunity')",
            name="ck_insight_valid_type",
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_insight_valid_confidence",
        ),
        sa.CheckConstraint("rating IN (1, -1)", name="ck_insight_valid_rating"),
    )

    # 7. insight_paper
    op.create_table(
        "insight_paper",
        sa.Column("insight_id", sa.Integer, sa.ForeignKey("insight.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True),
    )

    # 8. chat_session
    op.create_table(
        "chat_session",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=True),
        sa.Column("scope", sa.String(10), server_default="paper", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "(scope = 'paper' AND paper_id IS NOT NULL) OR (scope = 'corpus')",
            name="ck_chat_session_scope_paper_check",
        ),
        sa.CheckConstraint(
            "scope IN ('paper', 'corpus')",
            name="ck_chat_session_valid_scope",
        ),
    )

    # 9. chat_message
    op.create_table(
        "chat_message",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("chat_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_chat_message_valid_role",
        ),
    )

    # 10. processing_event
    op.create_table(
        "processing_event",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("paper_id", sa.Uuid, sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.Text, nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- Indexes ---
    op.create_index("idx_paper_fts", "paper", ["search_vector"], postgresql_using="gin")
    op.create_index("idx_paper_status", "paper", ["status"])
    op.create_index("idx_paper_created", "paper", [sa.text("created_at DESC")])
    op.execute(
        "CREATE INDEX idx_embedding_vec ON paper_embedding "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.create_index("idx_crossref_a", "cross_reference", ["paper_a"])
    op.create_index("idx_crossref_b", "cross_reference", ["paper_b"])
    op.create_index("idx_insight_type", "insight", ["type"])
    op.create_index("idx_insight_paper_paper", "insight_paper", ["paper_id"])
    op.create_index("idx_chat_paper", "chat_session", ["paper_id"])
    op.create_index("idx_processing_paper", "processing_event", ["paper_id", sa.text("created_at DESC")])
    op.create_index("idx_tag_category", "tag", ["category"])


def downgrade() -> None:
    op.drop_index("idx_tag_category")
    op.drop_index("idx_processing_paper")
    op.drop_index("idx_chat_paper")
    op.drop_index("idx_insight_paper_paper")
    op.drop_index("idx_insight_type")
    op.drop_index("idx_crossref_b")
    op.drop_index("idx_crossref_a")
    op.execute("DROP INDEX IF EXISTS idx_embedding_vec")
    op.drop_index("idx_paper_created")
    op.drop_index("idx_paper_status")
    op.drop_index("idx_paper_fts")

    op.drop_table("processing_event")
    op.drop_table("chat_message")
    op.drop_table("chat_session")
    op.drop_table("insight_paper")
    op.drop_table("insight")
    op.drop_table("cross_reference")
    op.drop_table("paper_embedding")
    op.drop_table("paper_tag")
    op.drop_table("paper")
    op.drop_table("tag")

    op.execute("DROP EXTENSION IF EXISTS vector")
