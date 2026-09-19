"""Contextual passages: a derived semantic representation of source text.

Adds a table only. The `embeddings` table already carries a polymorphic
(owner_type, owner_id), so contextual passages become a new owner_type
alongside content_unit with no change to embeddings at all -- and the
existing 830 baseline embeddings are untouched, so the two representations
can be compared.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-17
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE contextual_passages (
            container_id UUID NOT NULL,
            sequence_number INTEGER NOT NULL,
            source_unit_ids JSONB NOT NULL,
            first_unit_sequence INTEGER NOT NULL,
            last_unit_sequence INTEGER NOT NULL,
            unit_count INTEGER NOT NULL,
            text_content TEXT NOT NULL,
            text_tier VARCHAR(16) NOT NULL,
            grouping_config VARCHAR(64) NOT NULL,
            source_hash VARCHAR(64) NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_contextual_passage_identity
                UNIQUE (container_id, sequence_number, grouping_config),
            FOREIGN KEY(container_id) REFERENCES containers (id)
        )
    """)
    op.execute(
        "CREATE INDEX ix_contextual_passages_container_id "
        "ON contextual_passages (container_id)"
    )
    op.execute(
        "CREATE INDEX ix_contextual_passages_source_hash ON contextual_passages (source_hash)"
    )
    op.execute(
        "CREATE INDEX ix_contextual_passages_grouping_config "
        "ON contextual_passages (grouping_config)"
    )
    op.execute(
        "CREATE INDEX ix_contextual_passages_text_tier ON contextual_passages (text_tier)"
    )


def downgrade() -> None:
    # Drop the passages' embeddings first; they reference passages by
    # owner_id without a foreign key.
    op.execute("DELETE FROM embeddings WHERE owner_type = 'contextual_passage'")
    op.execute("DROP TABLE IF EXISTS contextual_passages")
