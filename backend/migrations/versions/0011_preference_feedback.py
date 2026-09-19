"""Explicit preference feedback: the user's verdict on Noema's inference.

Two new tables and nothing else. No existing table is altered, no canonical
content row is touched, and the rating columns in particular are left exactly
as they are -- keeping explicit feedback out of `user_content_interactions`
is the whole point of a separate table, not an accident of scope.

  user_preference_feedback          the current verdict, one row per
                                    (user, target_kind, concept). Unique, so
                                    "what do they say now" has one answer.

  user_preference_feedback_events   append-only history, so changing one's
                                    mind adds a fact instead of erasing one.

`target_kind` is a discriminator with a single admitted value today
(`'concept'`). It exists because feedback from a work page, a recommendation
or the library is a stated direction for this model, and adding those later
should mean widening a constraint and adding a nullable id column rather than
reinterpreting rows already written. `concept_id` is therefore nullable with
a check that ties it to its kind.

The foreign key to `concepts` carries no ON DELETE. The shared vocabulary is
not deleted, and one user's opinion must never be a reason to cascade into
canonical content. The foreign key to `users` does cascade: deleting an
account removes what that account said.

Nothing about the dashboard itself is persisted here. It stays derived on
every request.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preference_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_kind",
            sa.String(24),
            nullable=False,
            server_default="concept",
        ),
        # Nullable so a future target kind can use its own column. The check
        # below makes it required for the kind that exists now.
        sa.Column("concept_id", UUID(as_uuid=True), sa.ForeignKey("concepts.id")),
        sa.Column("feedback_type", sa.String(16), nullable=False),
        sa.Column("source_surface", sa.String(32), nullable=False),
        sa.Column("submission_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "first_recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "user_id", "target_kind", "concept_id", name="uq_user_preference_feedback"
        ),
        sa.CheckConstraint(
            "feedback_type IN ('confirmed', 'corrected')",
            name="ck_user_preference_feedback_type",
        ),
        sa.CheckConstraint(
            "target_kind IN ('concept')", name="ck_user_preference_feedback_target"
        ),
        sa.CheckConstraint(
            "target_kind <> 'concept' OR concept_id IS NOT NULL",
            name="ck_user_preference_feedback_target_present",
        ),
        sa.CheckConstraint(
            "submission_count >= 1", name="ck_user_preference_feedback_count"
        ),
    )
    op.create_index(
        "ix_user_preference_feedback_user_id", "user_preference_feedback", ["user_id"]
    )
    op.create_index(
        "ix_user_preference_feedback_concept_id",
        "user_preference_feedback",
        ["concept_id"],
    )
    op.create_index(
        "ix_user_preference_feedback_target_kind",
        "user_preference_feedback",
        ["target_kind"],
    )
    op.create_index(
        "ix_user_preference_feedback_feedback_type",
        "user_preference_feedback",
        ["feedback_type"],
    )

    op.create_table(
        "user_preference_feedback_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "feedback_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user_preference_feedback.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Null on the first answer: there was nothing to change from.
        sa.Column("feedback_before", sa.String(16)),
        sa.Column("feedback_after", sa.String(16), nullable=False),
        sa.Column("source_surface", sa.String(32), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("extra_metadata", JSONB),
        sa.Column("note", sa.Text),
        sa.CheckConstraint(
            "feedback_after IN ('confirmed', 'corrected')",
            name="ck_user_preference_feedback_event_type",
        ),
    )
    op.create_index(
        "ix_user_preference_feedback_events_feedback_id",
        "user_preference_feedback_events",
        ["feedback_id"],
    )
    op.create_index(
        "ix_user_preference_feedback_events_occurred_at",
        "user_preference_feedback_events",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("user_preference_feedback_events")
    op.drop_table("user_preference_feedback")
