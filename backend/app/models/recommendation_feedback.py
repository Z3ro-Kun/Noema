"""What a reader says about a recommendation, which is not what they think of the work.

Noema already records four things a reader can say, and this is a fifth. The
distinctions are the whole point of a separate table, so they are worth
writing down beside it:

    rating                 how much did I like this work?
    preference feedback    is this inferred pattern about me correct?
    library removal        I no longer want this on my shelf
    abandonment            I stopped consuming this
    not interested         do not recommend this work to me

**"Not interested" is none of the others.** It is not a low rating, not a
dislike of the work, not a dislike of its concepts, not a dislike of its
medium, and not a statement that the recommendation reasoning was wrong. A
reader can be entirely uninterested in a work they would rate highly if they
ever read it -- they have read the premise, it is not for them, and they
would like the shelf to move on.

So it lives here rather than anywhere it could be mistaken for evidence. It
never reaches `preference_evidence`, the taste dashboard, rating
normalization, established preferences, `WorkConcept.confidence`, semantic
search or library state. The one thing it does is remove a work from one
reader's recommendation candidates, and a test asserts each of those in turn.

---

One row, and what its absence means

Presence of a row is the suppression. Taking it back deletes the row, which
is why there is no event trail here and no soft-delete flag: unlike a verdict
on an inference, this is a switch, and "they once dismissed this and changed
their mind" is not a fact the product has any use for yet. If that changes,
an events table joins this one the way `user_preference_feedback_events`
joins its parent -- it does not get bolted on as a nullable column.

`action` exists with one admitted value so a second gesture ("seen it",
"maybe later") can widen a check constraint later rather than reinterpreting
rows already written. That is the same reason `target_kind` exists on
preference feedback, and it is deliberately not a generic action framework.
"""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# The only gesture this phase admits: "do not recommend this work to me".
ACTION_NOT_INTERESTED = "not_interested"
RECOMMENDATION_FEEDBACK_ACTIONS = (ACTION_NOT_INTERESTED,)


class UserRecommendationFeedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One reader's standing instruction about one work's recommendability."""

    __tablename__ = "user_recommendation_feedback"
    __table_args__ = (
        # One row per (user, work). Saying it twice is saying it once, which
        # is what makes the endpoint idempotent without a read-modify-write.
        UniqueConstraint("user_id", "work_id", name="uq_user_recommendation_feedback"),
        CheckConstraint(
            "action IN ('not_interested')", name="ck_user_recommendation_feedback_action"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Cascades on delete because a suppression is about a work, and a work
    # that no longer exists cannot be suppressed. Note the asymmetry with the
    # user side: deleting an account removes what it said, and deleting a
    # work removes what was said *about* it -- neither direction ever
    # cascades from user opinion into canonical content.
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )

    action: Mapped[str] = mapped_column(
        String(24),
        default=ACTION_NOT_INTERESTED,
        server_default=ACTION_NOT_INTERESTED,
        index=True,
    )

    user: Mapped["User"] = relationship()  # noqa: F821
    work: Mapped["Work"] = relationship()  # noqa: F821
