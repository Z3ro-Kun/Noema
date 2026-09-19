"""Work-level concept associations, plus a stable slug on the concept vocabulary.

Two changes, both minimal:

1. `concepts.slug` -- a stable identity alongside the display name, mirroring
   `domains.slug`. This is an ALTER on a canonical table, so it needs a
   reason: the vocabulary is keyed by slug in code, and without one, matching
   would have to be on `name`. Reword a concept's display name later
   ("Psychological Depth" -> "Psychological Focus") and a name-keyed lookup
   orphans the existing row and inserts a duplicate -- the exact vocabulary
   drift the concept vocabulary exists to prevent. The table is empty (the
   Phase 0 scaffolding was never populated), so backfilling it is
   unnecessary and NOT NULL is safe immediately.

2. `work_concepts` -- a new association table. Nothing else is touched: no
   canonical content row is copied, rewritten, re-embedded or deleted, and
   the user/library tables from Phase 1L are untouched.

`content_concepts` is deliberately left exactly as it is. It answers a
different question (where a concept occurs within a passage) and a later
phase may populate it; replacing it was never the intent.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The concepts table has never held a row, so there is nothing to
    # backfill and no window in which the column would be null.
    op.execute("ALTER TABLE concepts ADD COLUMN slug VARCHAR(128)")
    op.execute("UPDATE concepts SET slug = id::text WHERE slug IS NULL")
    op.execute("ALTER TABLE concepts ALTER COLUMN slug SET NOT NULL")
    op.execute("CREATE UNIQUE INDEX ix_concepts_slug ON concepts (slug)")

    op.create_table(
        "work_concepts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # CASCADE from works: an association is meaningless without its work.
        # There is no cascade in the other direction -- user data and shared
        # vocabulary must never be a reason to delete canonical content.
        sa.Column(
            "work_id",
            UUID(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "concept_id", UUID(as_uuid=True), sa.ForeignKey("concepts.id"), nullable=False
        ),
        # Non-null: an association with no attribution would be a bare claim.
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        # The source's own stated relevance rescaled to 0-1, or NULL when the
        # source states none. Not a probability; see the model docstring.
        sa.Column("confidence", sa.Float),
        # The original labels that supported this concept, so collapsing
        # several labels onto one concept never loses provenance.
        sa.Column("supporting_labels", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("work_id", "concept_id", name="uq_work_concept"),
        sa.CheckConstraint(
            "source IN ('source', 'computed', 'user')", name="ck_work_concept_source"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_work_concept_confidence",
        ),
    )
    op.create_index("ix_work_concepts_work_id", "work_concepts", ["work_id"])
    op.create_index("ix_work_concepts_concept_id", "work_concepts", ["concept_id"])
    op.create_index("ix_work_concepts_source", "work_concepts", ["source"])
    op.create_index("ix_work_concepts_method", "work_concepts", ["method"])


def downgrade() -> None:
    op.drop_table("work_concepts")
    op.execute("DROP INDEX IF EXISTS ix_concepts_slug")
    op.execute("ALTER TABLE concepts DROP COLUMN slug")
