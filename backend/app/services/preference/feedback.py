"""Reads and writes over a user's explicit preference feedback.

Phase 1X. The other half of the preference story: every module beside this
one turns behaviour into an interpretation, and this one records what the
user says back about that interpretation.

    evidence.py     what a rating means
    taste.py        which features a history supports
    profile.py      which of those a profile shows
    dashboard.py    how they are grouped for a person
    feedback.py     what the person said about the grouping

Every function takes a `user_id` and filters on it. That is the isolation
boundary, the same one `library_service` draws: there is no call here that
can reach another user's feedback, so a route cannot leak one by forgetting a
filter.

---

What this layer refuses

**It never writes a rating.** Nothing in this module touches
`user_content_interactions`, and a grep for `rating` finds nothing. A
disagreement is not a low score.

**It never infers a preference from a disagreement.** `corrected` means the
user rejected an interpretation. It does not mean they dislike the concept --
they may be indifferent, or may have liked the works for another reason
entirely. Turning "not really" into negative evidence would be inventing an
opinion the user did not express, which is the same error in the opposite
direction from the one the feedback is correcting.

**It never feeds the preference engine.** Phase 1X stores feedback and stops.
`build_preference_profile`, `build_taste_profile` and everything downstream
run exactly as they did in Phase 1W and do not read these tables. Whether
explicit feedback should shift inferred preference -- and by how much, and
after how many disagreements -- is a modelling question with its own phase
ahead of it. Answering it by quietly multiplying an evidence term here would
make the engine's output unexplainable and unmeasurable at the same time.

---

The target is a canonical concept

`concept_slug` resolves to a `Concept` row or the write is refused. A user
cannot record an opinion about a concept that does not exist, which keeps the
table joinable and stops the slug from becoming a free-text field by
accident.

Combinations are not yet targetable; see `app/models/feedback.py`. A request
naming a combination key ("mystery+psychological") fails the concept lookup
and is refused like any other unknown slug, which is the correct outcome
today: there is no stable answer to record it against.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import clock
from app.models import (
    FEEDBACK_SURFACES,
    FEEDBACK_TYPES,
    SURFACE_TASTE_PROFILE,
    TARGET_CONCEPT,
    Concept,
    UserPreferenceFeedback,
    UserPreferenceFeedbackEvent,
)


class FeedbackError(Exception):
    """Base class for refusals in the feedback layer."""


class UnknownTargetError(FeedbackError):
    """The slug does not name a concept in the canonical vocabulary."""


class InvalidFeedbackError(FeedbackError):
    """The value is not one of the recognised verdicts."""


class InvalidSurfaceError(FeedbackError):
    """The surface is not one this build knows how to attribute."""


def _now() -> datetime:
    """The event clock: strictly increasing, so history is a sequence.

    Not `datetime.now()`. See `app.core.clock`.
    """
    return clock.now()


async def _concept_by_slug(session: AsyncSession, slug: str) -> Concept | None:
    result = await session.execute(select(Concept).where(Concept.slug == slug))
    return result.scalar_one_or_none()


async def get_feedback(
    session: AsyncSession, *, user_id: uuid.UUID, concept_id: uuid.UUID
) -> UserPreferenceFeedback | None:
    """This user's current verdict on one concept, or None."""
    result = await session.execute(
        select(UserPreferenceFeedback)
        .options(selectinload(UserPreferenceFeedback.concept))
        .where(
            UserPreferenceFeedback.user_id == user_id,
            UserPreferenceFeedback.target_kind == TARGET_CONCEPT,
            UserPreferenceFeedback.concept_id == concept_id,
        )
    )
    return result.scalar_one_or_none()


async def record_feedback(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    concept_slug: str,
    feedback_type: str,
    source_surface: str = SURFACE_TASTE_PROFILE,
    note: str | None = None,
) -> UserPreferenceFeedback:
    """Record what this user says about Noema's reading of one concept.

    Upserts the current verdict and appends an event either way -- including
    when the answer is the same as last time, because answering again is
    itself a fact and a later phase may care that it was reaffirmed rather
    than left alone.
    """
    if feedback_type not in FEEDBACK_TYPES:
        raise InvalidFeedbackError(f"unknown feedback value {feedback_type!r}")
    if source_surface not in FEEDBACK_SURFACES:
        raise InvalidSurfaceError(f"unknown feedback surface {source_surface!r}")

    concept = await _concept_by_slug(session, concept_slug)
    if concept is None:
        raise UnknownTargetError(f"no concept with slug {concept_slug!r}")

    existing = await get_feedback(session, user_id=user_id, concept_id=concept.id)
    now = _now()

    if existing is None:
        feedback = UserPreferenceFeedback(
            user_id=user_id,
            target_kind=TARGET_CONCEPT,
            concept_id=concept.id,
            feedback_type=feedback_type,
            source_surface=source_surface,
            submission_count=1,
            first_recorded_at=now,
            updated_at=now,
        )
        session.add(feedback)
        # The event needs the row's id, and the caller needs the row back
        # with its concept attached.
        await session.flush()
        before: str | None = None
    else:
        feedback = existing
        before = feedback.feedback_type
        feedback.feedback_type = feedback_type
        feedback.source_surface = source_surface
        feedback.submission_count += 1
        # Strictly later than what the row already holds, and never merely
        # equal to it. An equal assignment reads as *unchanged* to the ORM,
        # which then lets the column's server-side `onupdate` write the value
        # instead -- and a server-written value leaves the attribute expired,
        # so the caller projecting this row would lazy-load it on an async
        # session and fail. The event clock makes this true on its own within
        # a process; saying it here makes the row's own invariant hold
        # regardless of which process last wrote it.
        feedback.updated_at = max(now, feedback.updated_at + clock.TICK)

    session.add(
        UserPreferenceFeedbackEvent(
            feedback_id=feedback.id,
            feedback_before=before,
            feedback_after=feedback_type,
            source_surface=source_surface,
            occurred_at=now,
            note=note,
        )
    )
    await session.flush()
    # `concept` is already loaded above; attach it so a caller projecting the
    # row does not trigger a lazy load on an async session.
    feedback.concept = concept
    return feedback


async def list_feedback(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[UserPreferenceFeedback]:
    """Everything this user has said, ordered by concept slug.

    Ordered by the canonical slug rather than by recency so that two
    identical histories produce identical payloads -- the same determinism
    rule the dashboard follows.
    """
    result = await session.execute(
        select(UserPreferenceFeedback)
        .options(selectinload(UserPreferenceFeedback.concept))
        .join(Concept, Concept.id == UserPreferenceFeedback.concept_id)
        .where(
            UserPreferenceFeedback.user_id == user_id,
            UserPreferenceFeedback.target_kind == TARGET_CONCEPT,
        )
        .order_by(Concept.slug)
    )
    return list(result.scalars().all())


async def feedback_for_slug(
    session: AsyncSession, *, user_id: uuid.UUID, concept_slug: str
) -> UserPreferenceFeedback | None:
    """This user's current verdict on a concept named by slug."""
    concept = await _concept_by_slug(session, concept_slug)
    if concept is None:
        raise UnknownTargetError(f"no concept with slug {concept_slug!r}")
    return await get_feedback(session, user_id=user_id, concept_id=concept.id)


async def feedback_history(
    session: AsyncSession, *, user_id: uuid.UUID, concept_slug: str, limit: int = 100
) -> list[UserPreferenceFeedbackEvent]:
    """Every answer this user has given about one concept, oldest first.

    Empty when they have never answered -- which is not the same as having
    answered and been ignored, and is why the current verdict is returned
    beside it rather than inferred from the last event.
    """
    feedback = await feedback_for_slug(
        session, user_id=user_id, concept_slug=concept_slug
    )
    if feedback is None:
        return []

    result = await session.execute(
        select(UserPreferenceFeedbackEvent)
        .where(UserPreferenceFeedbackEvent.feedback_id == feedback.id)
        .order_by(
            UserPreferenceFeedbackEvent.occurred_at, UserPreferenceFeedbackEvent.id
        )
        .limit(limit)
    )
    return list(result.scalars().all())


__all__ = [
    "FeedbackError",
    "InvalidFeedbackError",
    "InvalidSurfaceError",
    "UnknownTargetError",
    "feedback_for_slug",
    "feedback_history",
    "get_feedback",
    "list_feedback",
    "record_feedback",
]
