"""What a user says about Noema's reading of their taste.

Everything else in this package records *behaviour*: what someone added,
started, finished and rated. The preference engine turns that into an
interpretation -- "positive preference evidence for Psychological Depth".
This table records the other direction: the user's answer to that
interpretation.

    UserContentInteraction      the user did something
    preference evidence (1O)    Noema's reading of what they did
    UserPreferenceFeedback      the user's verdict on that reading

**This is not a rating, and must never become one.** "Not really" against
*You particularly enjoy fantasy* does not mean the user rated a fantasy work
badly, and it does not mean they dislike fantasy -- it may mean they are
indifferent to it, or that the works behind the finding were liked for some
other reason. It is a disagreement with an inference, which is a different
fact from a preference, and it lives in its own table precisely so the two
can never be confused by a later query. Writing it into
`user_content_interactions.rating` would corrupt the user's own rating
distribution, which the normalization layer depends on.

Two tables, mirroring the library's split for the same reasons:

  UserPreferenceFeedback        the *current* verdict, one row per
                                (user, concept). Every read answers from
                                here, so "what does this user currently say
                                about Tragedy" is one indexed lookup.

  UserPreferenceFeedbackEvent   an append-only trail. Someone who agrees
                                today and disagrees next month has told us
                                two different things at two different times,
                                and overwriting a boolean would erase the
                                first.

---

What the feedback attaches to

A `Concept` row -- the canonical vocabulary entry, keyed by a stable slug
since Phase 1M. Deliberately **not** the display string a dashboard rendered,
not a position in a list, not the set of supporting works, and not the
dashboard object itself. All four of those are recomputed on every request
and none of them is an identity.

Combinations ("Mystery + Psychological") are not yet targetable. Their key is
derived from two canonical slugs and is stable enough in principle, but a
pair exists only while the aggregation layer admits it, and the product has
not yet decided what disagreeing with a pair means. `target_kind` records
what a row is about so that answer can be added later without reinterpreting
existing rows; today the check constraint admits one value.

Where the feedback came from is recorded too (`source_surface`). The taste
profile is the only surface that asks today, but a work page, a
recommendation and the library are all places the same question will
eventually be asked, and which one it was cannot be recovered afterwards.
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

# The user agrees that Noema's reading is reasonable.
FEEDBACK_CONFIRMED = "confirmed"
# The user does not. Note what this is *not*: it is not evidence of a
# negative preference. Phase 1X stores disagreement and nothing more. If the
# product later asks "so do you dislike it?" that answer is a third value,
# recorded separately, and never inferred from this one.
FEEDBACK_CORRECTED = "corrected"

FEEDBACK_TYPES = (FEEDBACK_CONFIRMED, FEEDBACK_CORRECTED)

# What a feedback row is about. One value today; see the module docstring.
TARGET_CONCEPT = "concept"
FEEDBACK_TARGET_KINDS = (TARGET_CONCEPT,)

# Where the question was asked. Kept because it is unrecoverable later and
# because feedback from a recommendation will not mean the same thing as
# feedback from a profile.
SURFACE_TASTE_PROFILE = "taste_profile"
FEEDBACK_SURFACES = (SURFACE_TASTE_PROFILE,)


class UserPreferenceFeedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One user's current verdict on one canonical concept."""

    __tablename__ = "user_preference_feedback"
    __table_args__ = (
        # One verdict per user per concept. Re-answering updates this row and
        # appends an event; it never accumulates rows, so "what do they say
        # now" has exactly one answer.
        UniqueConstraint(
            "user_id", "target_kind", "concept_id", name="uq_user_preference_feedback"
        ),
        CheckConstraint(
            "feedback_type IN ('confirmed', 'corrected')",
            name="ck_user_preference_feedback_type",
        ),
        CheckConstraint(
            "target_kind IN ('concept')", name="ck_user_preference_feedback_target"
        ),
        # A concept target needs a concept. When another target kind is added
        # its own column joins this constraint rather than replacing it.
        CheckConstraint(
            "target_kind <> 'concept' OR concept_id IS NOT NULL",
            name="ck_user_preference_feedback_target_present",
        ),
        CheckConstraint(
            "submission_count >= 1", name="ck_user_preference_feedback_count"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    target_kind: Mapped[str] = mapped_column(
        String(24), default=TARGET_CONCEPT, server_default=TARGET_CONCEPT, index=True
    )
    # No ON DELETE: the shared vocabulary is not deleted, and one user's
    # opinion must never be a reason to cascade into canonical content.
    concept_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id"), index=True
    )

    feedback_type: Mapped[str] = mapped_column(String(16), index=True)
    source_surface: Mapped[str] = mapped_column(String(32))

    # How many times this user has answered about this concept, including the
    # current answer. Cheap to read beside the verdict; the events remain the
    # authority on what those answers were.
    submission_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1"
    )

    first_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship()  # noqa: F821
    concept: Mapped["Concept"] = relationship()  # noqa: F821
    events: Mapped[list["UserPreferenceFeedbackEvent"]] = relationship(
        back_populates="feedback",
        cascade="all, delete-orphan",
        order_by="UserPreferenceFeedbackEvent.occurred_at",
    )


class UserPreferenceFeedbackEvent(Base, UUIDPrimaryKeyMixin):
    """One recorded answer. Append-only; never updated or deleted.

    Before and after are both stored, so a single row says what the user
    changed their mind *from* without replaying anything. The first answer
    has a null `feedback_before`.
    """

    __tablename__ = "user_preference_feedback_events"
    __table_args__ = (
        CheckConstraint(
            "feedback_after IN ('confirmed', 'corrected')",
            name="ck_user_preference_feedback_event_type",
        ),
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_preference_feedback.id", ondelete="CASCADE"),
        index=True,
    )

    feedback_before: Mapped[str | None] = mapped_column(String(16))
    feedback_after: Mapped[str] = mapped_column(String(16))
    source_surface: Mapped[str] = mapped_column(String(32))

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Room for what a later phase may want to know about an answer -- which
    # group the item was in when it was shown, say -- without a migration to
    # ask. Deliberately not the dashboard object: that is derived state, and
    # storing it would make one request's output durable.
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)

    feedback: Mapped["UserPreferenceFeedback"] = relationship(back_populates="events")
