"""Users, sessions, and the user/canonical-content boundary.

Four new tables, and **no change to any existing one**. That is the point of
the design rather than an accident of it: a user's library is a set of
references to canonical works, so establishing it needs nothing added to
`works`, `containers`, `content_units`, `embeddings` or `text_sources`. No
canonical row is copied, rewritten or re-embedded by this migration.

  users                        an account
  user_sessions                revocable bearer tokens, stored hashed
  user_content_interactions    one row per (user, work): the current state
  user_content_events          append-only history of the transitions

The unique constraint on (user_id, work_id) is what structurally prevents a
library from becoming per-user copies of content: a user can hold at most
one reference to a work, and the reference carries only their own state.

Ratings are nullable and constrained separately from status, because unrated
is not a low rating and a completion is not a positive one.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        # scrypt$n$r$p$salt$hash -- the work factors travel with the hash so
        # they can be raised later without invalidating existing rows.
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(128)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SHA-256 of the token. The plaintext is returned once and never stored.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)

    op.create_table(
        "user_content_interactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # No ON DELETE. Canonical content is shared and is never removed
        # because of user data; a library must not be able to cascade into
        # the corpus.
        sa.Column(
            "work_id", UUID(as_uuid=True), sa.ForeignKey("works.id"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        # Null means unrated. Deliberately independent of status.
        sa.Column("rating", sa.Integer),
        sa.Column("rated_at", sa.DateTime(timezone=True)),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("abandoned_at", sa.DateTime(timezone=True)),
        # Soft removal: history and ratings survive leaving the library.
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("times_started", sa.Integer, nullable=False, server_default="0"),
        sa.Column("times_completed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "work_id", name="uq_user_content_interaction"),
        sa.CheckConstraint(
            "status IN ('planned', 'in_progress', 'on_hold', 'completed', 'abandoned')",
            name="ck_user_content_interaction_status",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 10)",
            name="ck_user_content_interaction_rating",
        ),
        sa.CheckConstraint(
            "times_started >= 0 AND times_completed >= 0",
            name="ck_user_content_interaction_counts",
        ),
    )
    op.create_index(
        "ix_user_content_interactions_user_id", "user_content_interactions", ["user_id"]
    )
    op.create_index(
        "ix_user_content_interactions_work_id", "user_content_interactions", ["work_id"]
    )
    op.create_index(
        "ix_user_content_interactions_status", "user_content_interactions", ["status"]
    )
    op.create_index(
        "ix_user_content_interactions_rating", "user_content_interactions", ["rating"]
    )
    op.create_index(
        "ix_user_content_interactions_removed_at", "user_content_interactions", ["removed_at"]
    )

    op.create_table(
        "user_content_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "interaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user_content_interactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("status_before", sa.String(16)),
        sa.Column("status_after", sa.String(16)),
        sa.Column("rating_before", sa.Integer),
        sa.Column("rating_after", sa.Integer),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("extra_metadata", JSONB),
        sa.Column("note", sa.Text),
        sa.CheckConstraint(
            "event_type IN ('added', 'removed', 'status_changed', 'rating_changed')",
            name="ck_user_content_event_type",
        ),
    )
    op.create_index(
        "ix_user_content_events_interaction_id", "user_content_events", ["interaction_id"]
    )
    op.create_index("ix_user_content_events_event_type", "user_content_events", ["event_type"])
    op.create_index("ix_user_content_events_occurred_at", "user_content_events", ["occurred_at"])


def downgrade() -> None:
    # Reverse creation order so the foreign keys unwind cleanly. This drops
    # user data only; no canonical content is touched either way.
    op.drop_table("user_content_events")
    op.drop_table("user_content_interactions")
    op.drop_table("user_sessions")
    op.drop_table("users")
