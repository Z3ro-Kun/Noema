"""A content unit may hang off a Work directly, when no container fits.

The model so far said every unit lives in a container, and for narrative text
that is right: a paragraph belongs to a chapter, an episode summary belongs to
an episode. But corpus expansion turned up works where the canonical source
(AniList) catalogues *no* containers at all -- a webtoon with no volume list,
a series AniList knows only as a title -- while Wikipedia carries a perfectly
legitimate plot summary describing the whole thing.

Those works were invisible to semantic search, because a work with no
containers can hold no units and a work with no units has nothing to embed.
The two ways out were both wrong: inventing a "Volume 1" container to hang the
summary on would be fabricated structure presented as source fact, and
loosening the number-plus-title matching would attach text to the wrong
episode. So the third way: let the unit say it describes the *work*.

    container_id  -> this text belongs inside that chapter/episode/volume
    work_id       -> this text describes the work, and no container fits

Exactly one, never both, never neither -- enforced by a check constraint
rather than left to the application, because a unit with two parents makes
"which work is this?" have two answers, and every retrieval query resolves
that question by joining.

`container_id` becomes nullable and nothing else about existing rows changes:
every unit in the table today has a container and keeps it, and the new column
is null for all of them.

The downgrade drops `work_id`, which discards any work-level summaries that
exist at the time, and restores NOT NULL on `container_id`, which fails
outright if any remain. That is deliberate: silently re-parenting a work-level
summary into some container would be the fabrication this migration exists to
avoid.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-21
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE content_units ALTER COLUMN container_id DROP NOT NULL")
    op.execute("ALTER TABLE content_units ADD COLUMN work_id UUID")
    op.execute(
        "ALTER TABLE content_units ADD CONSTRAINT content_units_work_id_fkey "
        "FOREIGN KEY (work_id) REFERENCES works (id)"
    )
    op.execute("CREATE INDEX ix_content_units_work_id ON content_units (work_id)")
    # num_nonnulls is Postgres' own counter, so the constraint reads as the
    # rule it enforces rather than as a pair of mirrored null tests.
    op.execute(
        "ALTER TABLE content_units ADD CONSTRAINT ck_content_unit_single_parent "
        "CHECK (num_nonnulls(container_id, work_id) = 1)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE content_units DROP CONSTRAINT ck_content_unit_single_parent")
    op.execute("DROP INDEX ix_content_units_work_id")
    op.execute("ALTER TABLE content_units DROP CONSTRAINT content_units_work_id_fkey")
    op.execute("ALTER TABLE content_units DROP COLUMN work_id")
    op.execute("ALTER TABLE content_units ALTER COLUMN container_id SET NOT NULL")
