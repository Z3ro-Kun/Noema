"""Experiment embeddings: vectors from candidate models, for offline comparison.

Additive only. `embeddings` is not touched, so the 384-d production baseline
stays exactly as it is and the model comparison remains reproducible.

The vector column is deliberately unconstrained rather than vector(768): a
later candidate may have a different width, and pgvector only objects when
two *different* widths meet in one distance comparison. Every read path
filters by model_name, which keeps that from happening.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-17
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE experiment_embeddings (
            owner_type VARCHAR(32) NOT NULL,
            owner_id UUID NOT NULL,
            model_name VARCHAR(128) NOT NULL,
            model_revision VARCHAR(64),
            vector VECTOR NOT NULL,
            source_hash VARCHAR(64) NOT NULL,
            dimension INTEGER NOT NULL,
            normalized BOOLEAN NOT NULL,
            prep_version INTEGER NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_experiment_embedding_owner_model
                UNIQUE (owner_type, owner_id, model_name)
        )
    """)
    for column in ("owner_type", "owner_id", "model_name", "source_hash"):
        op.execute(
            f"CREATE INDEX ix_experiment_embeddings_{column} "
            f"ON experiment_embeddings ({column})"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS experiment_embeddings")
