"""Widen production embeddings to 768 dimensions for all-mpnet-base-v2.

The existing vectors are deleted rather than converted. There is no
meaningful cast from a 384-d MiniLM vector to a 768-d MPNet one -- they are
different models' coordinate spaces, not the same values at a different
width -- so keeping them would leave production holding vectors that cannot
be compared with anything the new model produces. Requirement: a clean
replacement, not an ambiguous mixture.

Embeddings are derived data and are fully regenerable from ContentUnits, so
nothing is lost that cannot be rebuilt by re-running the embedding job. No
source text, provenance, or structural row is touched.

`experiment_embeddings` is deliberately left alone: its vector column is
unconstrained, it already holds the Phase 1F MPNet vectors, and keeping it
intact is what makes that experiment reproducible.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-17
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Clear first: ALTER cannot re-type rows whose width does not match.
    # This covers every owner_type, including the contextual_passage vectors
    # from the Phase 1E experiment, which were MiniLM vectors too.
    op.execute("DELETE FROM embeddings")
    op.execute("ALTER TABLE embeddings ALTER COLUMN vector TYPE vector(768)")


def downgrade() -> None:
    op.execute("DELETE FROM embeddings")
    op.execute("ALTER TABLE embeddings ALTER COLUMN vector TYPE vector(384)")
