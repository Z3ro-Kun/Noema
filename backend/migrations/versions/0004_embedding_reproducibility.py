"""Reproducibility metadata on embeddings.

Enough state to answer "is this vector still valid?" without re-running the
model: the hash of the text it was built from, which model build produced
it, and which text-preparation version was in effect.

The table is empty at this point (no embeddings have ever been generated),
so the new columns are added NOT NULL with defaults rather than requiring a
backfill.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-17
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE embeddings ADD COLUMN model_revision VARCHAR(64)")
    op.execute("ALTER TABLE embeddings ADD COLUMN source_hash VARCHAR(64) NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE embeddings ADD COLUMN dimension INTEGER NOT NULL DEFAULT 384")
    op.execute("ALTER TABLE embeddings ADD COLUMN normalized BOOLEAN NOT NULL DEFAULT true")
    op.execute("ALTER TABLE embeddings ADD COLUMN prep_version INTEGER NOT NULL DEFAULT 1")
    op.execute("CREATE INDEX ix_embeddings_source_hash ON embeddings (source_hash)")

    # No ANN index (ivfflat/hnsw) on purpose: at ~830 vectors an exact scan
    # is already sub-millisecond, and an approximate index would trade recall
    # for a speedup this corpus cannot measure. Revisit when the corpus is
    # large enough for exact search to actually hurt.


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_source_hash")
    for column in ("prep_version", "normalized", "dimension", "source_hash", "model_revision"):
        op.execute(f"ALTER TABLE embeddings DROP COLUMN IF EXISTS {column}")
