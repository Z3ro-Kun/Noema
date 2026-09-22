"""Recommendation feedback: "do not recommend this work to me".

One new table and nothing else. No existing table is altered, and in
particular nothing in the preference stack is touched -- which is the point.
Noema already records how much a reader liked a work, whether an inferred
pattern about them is right, whether they still want something on their shelf
and whether they stopped consuming it. This is a fifth, different thing, and
giving it its own table is what stops a later query from mistaking it for any
of the other four.

    user_recommendation_feedback   one row per (user, work). Its presence
                                   removes the work from that reader's
                                   recommendation candidates, and its absence
                                   is the only way to be recommendable again.

No events table. Unlike a verdict on an inference, this is a switch: taking
it back deletes the row, and "they dismissed this once and changed their
mind" is not yet a fact the product uses. When it becomes one, an events
table joins this one rather than a nullable column being bolted on.

`action` carries a single admitted value behind a check constraint, so a
second gesture later widens the constraint instead of reinterpreting rows
already written -- the same reasoning as `target_kind` in 0011.

Both foreign keys cascade, in opposite directions and for the same reason:
deleting an account removes what it said, and deleting a work removes what
was said about it. Neither direction ever cascades from a user's opinion into
canonical content.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-22
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_recommendation_feedback (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            work_id UUID NOT NULL,
            action VARCHAR(24) NOT NULL DEFAULT 'not_interested',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_user_recommendation_feedback UNIQUE (user_id, work_id),
            CONSTRAINT ck_user_recommendation_feedback_action
                CHECK (action IN ('not_interested')),
            CONSTRAINT user_recommendation_feedback_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT user_recommendation_feedback_work_id_fkey
                FOREIGN KEY (work_id) REFERENCES works (id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX ix_user_recommendation_feedback_user_id "
        "ON user_recommendation_feedback (user_id)"
    )
    op.execute(
        "CREATE INDEX ix_user_recommendation_feedback_work_id "
        "ON user_recommendation_feedback (work_id)"
    )
    op.execute(
        "CREATE INDEX ix_user_recommendation_feedback_action "
        "ON user_recommendation_feedback (action)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE user_recommendation_feedback")
