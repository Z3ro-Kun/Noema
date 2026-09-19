"""A user's relationship with canonical content.

This is the boundary this phase exists to draw. Canonical content -- works,
containers, content units, embeddings, provenance -- is shared and is never
copied per user. A user's library is a set of *references* to it, carrying
only what is true of that user.

    Work "Frankenstein"          <- one row, shared by everyone
      <- UserContentInteraction   user A: completed, 9/10
      <- UserContentInteraction   user B: in_progress, 8/10
      <- UserContentInteraction   user C: planned, unrated

Two tables, not one, and deliberately not full event sourcing:

  UserContentInteraction   the *current* state, one row per (user, work).
                           Every read answers from here, so the common
                           queries stay a single indexed lookup.

  UserContentEvent         an append-only trail of the transitions that
                           produced that state. It exists for one reason:
                           re-reading a book, or changing a rating, must not
                           erase the fact that the earlier reading or the
                           earlier rating happened.

Why not one table: a single row cannot distinguish "read once, rated 9" from
"read three times, rated 4 then 9". Why not event sourcing: the current
state is small, bounded and read constantly, so materializing it is simply
correct -- rebuilding it by folding a log on every request would buy
nothing here.

**Status and rating are independent axes, on purpose.** Finishing something
is not liking it, and the write path never infers one from the other. A
completion with no rating stays unrated; abandonment records no rating at
all. Interpreting any of this is the future taste layer's job, not the
storage layer's.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Where a user is with a work. A small controlled vocabulary, each value
# carrying evidence the others do not.
STATUS_PLANNED = "planned"  # in the library, not yet started
STATUS_IN_PROGRESS = "in_progress"  # started, still going
STATUS_ON_HOLD = "on_hold"  # paused, explicitly not given up on
STATUS_COMPLETED = "completed"  # reached the end at least once
STATUS_ABANDONED = "abandoned"  # stopped, not intending to resume

# `on_hold` exists to protect `abandoned`. Without it, every pause gets
# filed as abandonment and the one signal this phase is told to keep
# unambiguous becomes a mixture of "gave up" and "busy right now".
STATUSES = (
    STATUS_PLANNED,
    STATUS_IN_PROGRESS,
    STATUS_ON_HOLD,
    STATUS_COMPLETED,
    STATUS_ABANDONED,
)

# The rating scale the product offers. Fixed and constrained so that every
# stored rating is comparable *within* a user; comparing across users is the
# taste layer's problem and is answerable from a user's own rows, which is
# why no per-rating scale column is stored.
RATING_MIN = 1
RATING_MAX = 10

EVENT_ADDED = "added"
EVENT_REMOVED = "removed"
EVENT_STATUS_CHANGED = "status_changed"
EVENT_RATING_CHANGED = "rating_changed"
EVENT_TYPES = (EVENT_ADDED, EVENT_REMOVED, EVENT_STATUS_CHANGED, EVENT_RATING_CHANGED)


class UserContentInteraction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One user's current relationship with one canonical work."""

    __tablename__ = "user_content_interactions"
    __table_args__ = (
        # One row per user per work. This is what stops a library from
        # becoming per-user copies of canonical content.
        UniqueConstraint("user_id", "work_id", name="uq_user_content_interaction"),
        CheckConstraint(
            "status IN ('planned', 'in_progress', 'on_hold', 'completed', 'abandoned')",
            name="ck_user_content_interaction_status",
        ),
        CheckConstraint(
            f"rating IS NULL OR (rating >= {RATING_MIN} AND rating <= {RATING_MAX})",
            name="ck_user_content_interaction_rating",
        ),
        CheckConstraint(
            "times_started >= 0 AND times_completed >= 0",
            name="ck_user_content_interaction_counts",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # A reference to shared canonical content. No ON DELETE: canonical
    # content is not deleted, and a user's library must never be a reason to
    # cascade into it.
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id"), index=True
    )

    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_PLANNED, server_default=STATUS_PLANNED, index=True
    )

    # Null means *unrated*, which is not the same as rated badly and must
    # never be filled in from status. Only the user sets this.
    rating: Mapped[int | None] = mapped_column(Integer, index=True)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Library membership. `added_at` is the weak-interest signal on its own:
    # a row can exist with everything below it null, meaning "wanted to,
    # never opened it".
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Exposure: the user actually engaged with the content at least once.
    # Distinct from `added_at`, which is only intent.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Removal is soft. A user who completes something, rates it 9, then
    # tidies their library has still told us they liked it; hard-deleting
    # the row would destroy the strongest preference evidence the system
    # gets. `removed_at IS NULL` means "currently in the library".
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # Reconsumption, cheap to query without folding the event log. Repeated
    # engagement is behavioural preference evidence and is not inferrable
    # from a single `completed` status.
    times_started: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    times_completed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship()  # noqa: F821
    work: Mapped["Work"] = relationship()  # noqa: F821
    events: Mapped[list["UserContentEvent"]] = relationship(
        back_populates="interaction",
        cascade="all, delete-orphan",
        order_by="UserContentEvent.occurred_at",
    )

    @property
    def in_library(self) -> bool:
        return self.removed_at is None


class UserContentEvent(Base, UUIDPrimaryKeyMixin):
    """One recorded transition. Append-only; never updated or deleted.

    Before/after values are stored explicitly rather than reconstructed by
    replaying, so a single row is self-contained evidence: it says what
    changed, in which direction, and when.
    """

    __tablename__ = "user_content_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('added', 'removed', 'status_changed', 'rating_changed')",
            name="ck_user_content_event_type",
        ),
    )

    interaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_content_interactions.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(24), index=True)

    status_before: Mapped[str | None] = mapped_column(String(16))
    status_after: Mapped[str | None] = mapped_column(String(16))
    rating_before: Mapped[int | None] = mapped_column(Integer)
    rating_after: Mapped[int | None] = mapped_column(Integer)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Free-form room for things a later phase may want to know about the
    # transition (which client, whether it was a bulk import) without
    # needing a migration to ask.
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)

    interaction: Mapped["UserContentInteraction"] = relationship(back_populates="events")
