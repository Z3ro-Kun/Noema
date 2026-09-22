"""Reads and writes over a user's library.

Every function here takes a `user_id` and filters on it. That is the
isolation boundary: there is no call in this module that can reach another
user's interaction, so a route cannot leak one by forgetting a filter.

The write path records what the user did and nothing more. It never derives
a rating from a status, never assigns a score to abandonment, and never
decides that finishing something means liking it. Each state-changing call
also appends a `UserContentEvent`, so re-reading a work or changing a rating
adds to the history instead of overwriting it.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import clock
from app.models import (
    EVENT_ADDED,
    EVENT_RATING_CHANGED,
    EVENT_REMOVED,
    EVENT_STATUS_CHANGED,
    RATING_MAX,
    RATING_MIN,
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PLANNED,
    STATUSES,
    Domain,
    UserContentEvent,
    UserContentInteraction,
    Work,
)


class LibraryError(Exception):
    """Base class for refusals in the library layer."""


class WorkNotFoundError(LibraryError):
    """The referenced canonical work does not exist."""


class AlreadyInLibraryError(LibraryError):
    """The user already has a live library entry for this work."""


class InteractionNotFoundError(LibraryError):
    """This user has no interaction with this work.

    Raised identically whether the interaction never existed or belongs to
    someone else, so the API cannot be used to probe another user's library.
    """


class InvalidStatusError(LibraryError):
    """A status outside the controlled vocabulary."""


class InvalidRatingError(LibraryError):
    """A rating outside the offered scale."""


class ReconsumptionNotAvailableError(LibraryError):
    """Asked to record another completion of something not yet completed.

    Recording a re-read is only meaningful once there has been a read. The
    first completion is an ordinary status change; see `record_reconsumption`.
    """


class NoReconsumptionToUndoError(LibraryError):
    """Asked to take back a completion that is not there to take back.

    Raised at the floor -- a work completed once has nothing above the first
    reading to remove -- and when the stored trail does not end in a cycle
    this call would be able to undo cleanly.
    """


def _now() -> datetime:
    """The event clock: strictly increasing, so history is a sequence.

    Not `datetime.now()`. See `app.core.clock` -- events written inside one
    request would otherwise share a timestamp and the log would order by a
    random uuid.
    """
    return clock.now()


def _record(
    interaction: UserContentInteraction,
    session: AsyncSession,
    *,
    event_type: str,
    status_before: str | None = None,
    status_after: str | None = None,
    rating_before: int | None = None,
    rating_after: int | None = None,
    note: str | None = None,
) -> UserContentEvent:
    event = UserContentEvent(
        interaction_id=interaction.id,
        event_type=event_type,
        status_before=status_before,
        status_after=status_after,
        rating_before=rating_before,
        rating_after=rating_after,
        occurred_at=_now(),
        note=note,
    )
    session.add(event)
    return event


async def get_interaction(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID
) -> UserContentInteraction | None:
    """This user's interaction with this work, including soft-removed ones."""
    result = await session.execute(
        select(UserContentInteraction)
        .options(selectinload(UserContentInteraction.work).selectinload(Work.domain))
        .where(
            UserContentInteraction.user_id == user_id,
            UserContentInteraction.work_id == work_id,
        )
    )
    return result.scalar_one_or_none()


async def list_library(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    status: str | None = None,
    domain_slug: str | None = None,
    include_removed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[UserContentInteraction]:
    query = (
        select(UserContentInteraction)
        .options(selectinload(UserContentInteraction.work).selectinload(Work.domain))
        .where(UserContentInteraction.user_id == user_id)
        # Most recently touched first, and `work_id` to break the ties.
        #
        # `updated_at` is written by the column's server-side `onupdate`,
        # which is `now()` -- transaction-start time in Postgres, not
        # statement time. Two entries touched in one transaction therefore
        # hold the *same* instant, and on a tie the database is free to
        # return them in any order it likes: the same request twice could
        # page them differently, and an entry could appear on page one and
        # again on page two.
        #
        # `work_id` is the canonical identifier of what the entry points at,
        # and (user_id, work_id) is unique -- so it is stable across requests
        # and never itself ties. It changes nothing about what `updated_at`
        # means or how the library ranks; it only decides what happens after
        # the ranking has run out of things to say.
        .order_by(
            UserContentInteraction.updated_at.desc(),
            UserContentInteraction.work_id.asc(),
        )
    )
    if not include_removed:
        query = query.where(UserContentInteraction.removed_at.is_(None))
    if status is not None:
        if status not in STATUSES:
            raise InvalidStatusError(f"unknown status {status!r}")
        query = query.where(UserContentInteraction.status == status)
    if domain_slug is not None:
        query = query.join(Work, UserContentInteraction.work_id == Work.id).join(
            Domain, Work.domain_id == Domain.id
        ).where(Domain.slug == domain_slug)

    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


async def count_library(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    status: str | None = None,
    domain_slug: str | None = None,
    include_removed: bool = False,
) -> int:
    """How many entries match, independent of the page being asked for.

    Takes the same filters as `list_library` so the two can never disagree
    about what matched -- a count that ignores the filter is worse than no
    count, because it looks right.
    """
    query = (
        select(func.count())
        .select_from(UserContentInteraction)
        .where(UserContentInteraction.user_id == user_id)
    )
    if not include_removed:
        query = query.where(UserContentInteraction.removed_at.is_(None))
    if status is not None:
        if status not in STATUSES:
            raise InvalidStatusError(f"unknown status {status!r}")
        query = query.where(UserContentInteraction.status == status)
    if domain_slug is not None:
        query = query.join(Work, UserContentInteraction.work_id == Work.id).join(
            Domain, Work.domain_id == Domain.id
        ).where(Domain.slug == domain_slug)
    return (await session.execute(query)).scalar_one()


async def status_counts(
    session: AsyncSession, *, user_id: uuid.UUID
) -> dict[str, int]:
    """How many live entries this user holds in each status.

    One grouped query rather than one count per tab, and every status appears
    even at zero -- a tab that disappears when it empties is worse than one
    saying nothing is in it.
    """
    rows = await session.execute(
        select(UserContentInteraction.status, func.count())
        .where(
            UserContentInteraction.user_id == user_id,
            UserContentInteraction.removed_at.is_(None),
        )
        .group_by(UserContentInteraction.status)
    )
    counts = {status: 0 for status in STATUSES}
    for status, count in rows.all():
        counts[status] = count
    return counts


async def count_rated(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Live entries carrying a rating. Unrated is not a zero; it is nothing."""
    return (
        await session.execute(
            select(func.count())
            .select_from(UserContentInteraction)
            .where(
                UserContentInteraction.user_id == user_id,
                UserContentInteraction.removed_at.is_(None),
                UserContentInteraction.rating.is_not(None),
            )
        )
    ).scalar_one()


async def count_removed(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Soft-removed entries, whose ratings and history are still kept."""
    return (
        await session.execute(
            select(func.count())
            .select_from(UserContentInteraction)
            .where(
                UserContentInteraction.user_id == user_id,
                UserContentInteraction.removed_at.is_not(None),
            )
        )
    ).scalar_one()


async def add_to_library(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    work_id: uuid.UUID,
    status: str = STATUS_PLANNED,
) -> UserContentInteraction:
    """Reference a canonical work from this user's library.

    Never copies the work. Re-adding something previously removed revives
    the original row, so its history and any earlier rating survive.
    """
    if status not in STATUSES:
        raise InvalidStatusError(f"unknown status {status!r}")

    if await session.get(Work, work_id) is None:
        raise WorkNotFoundError("work not found")

    existing = await get_interaction(session, user_id=user_id, work_id=work_id)
    if existing is not None:
        if existing.removed_at is None:
            raise AlreadyInLibraryError("this work is already in the library")

        # Revive rather than insert: the unique constraint would reject a
        # second row anyway, and a new row would orphan the old history.
        existing.removed_at = None
        existing.added_at = _now()
        _record(existing, session, event_type=EVENT_ADDED, status_after=existing.status)
        await session.flush()
        return existing

    interaction = UserContentInteraction(
        user_id=user_id, work_id=work_id, status=status, added_at=_now()
    )
    session.add(interaction)
    await session.flush()

    _record(interaction, session, event_type=EVENT_ADDED, status_after=status)
    if status == STATUS_IN_PROGRESS:
        _apply_status(interaction, session, status)
    await session.flush()
    return interaction


async def remove_from_library(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID
) -> UserContentInteraction:
    """Soft-remove. The row and its history stay; library membership ends."""
    interaction = await get_interaction(session, user_id=user_id, work_id=work_id)
    if interaction is None or interaction.removed_at is not None:
        raise InteractionNotFoundError("no library entry for that work")

    interaction.removed_at = _now()
    _record(interaction, session, event_type=EVENT_REMOVED, status_before=interaction.status)
    await session.flush()
    return interaction


def _apply_status(
    interaction: UserContentInteraction, session: AsyncSession, status: str
) -> UserContentEvent:
    """Move to a status and stamp the dates that transition implies.

    Only dates. Nothing here touches `rating`: a completion is not a
    positive rating and an abandonment is not a negative one, and inferring
    either at write time would destroy the distinction the taste layer needs.
    """
    before = interaction.status
    now = _now()

    if status == STATUS_IN_PROGRESS:
        # Counts every start, including a restart after finishing. This is
        # what makes reconsumption visible without folding the event log.
        if before != STATUS_IN_PROGRESS:
            interaction.times_started += 1
        if interaction.started_at is None:
            interaction.started_at = now
    elif status == STATUS_COMPLETED:
        if before != STATUS_COMPLETED:
            interaction.times_completed += 1
        # Finishing something never started still means it was started.
        if interaction.started_at is None:
            interaction.started_at = now
            interaction.times_started += 1
        interaction.completed_at = now
    elif status == STATUS_ABANDONED:
        interaction.abandoned_at = now

    interaction.status = status
    return _record(
        interaction,
        session,
        event_type=EVENT_STATUS_CHANGED,
        status_before=before,
        status_after=status,
    )


async def set_status(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID, status: str
) -> UserContentInteraction:
    if status not in STATUSES:
        raise InvalidStatusError(f"unknown status {status!r}")

    interaction = await get_interaction(session, user_id=user_id, work_id=work_id)
    if interaction is None or interaction.removed_at is not None:
        raise InteractionNotFoundError("no library entry for that work")

    _apply_status(interaction, session, status)
    await session.flush()
    return interaction


async def record_reconsumption(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID
) -> UserContentInteraction:
    """Record one more deliberate completed cycle of a finished work.

    Phase 1AA. A re-read was previously something a reader had to *perform*:
    move back to `in_progress`, then mark completed again. That worked, and
    it is still exactly what this does -- but it made the count a side
    effect of navigating the status control, which is how a work ends up
    claiming two reads after one.

    So the cycle becomes a single deliberate act. This applies the same two
    transitions the manual route always did, through `_apply_status`, so:

        times_started    += 1     a re-read is a start
        times_completed  += 1     and a finish
        completed_at      = now
        status            = completed    where it began

    and the event log gets the same genuine pair it would have got by hand
    -- `completed -> in_progress` then `in_progress -> completed` -- which
    `library_product.build_history` already folds into "Started again" and
    "Completed". No new event type, no second counter, and no parallel
    mechanism: the stored history of a reader who used the old route and one
    who presses the new control is identical.

    The rating is not touched, read, or asked for. Finishing something a
    third time is not a score, and `_apply_status` has never derived one.

    Only a completed work can be reconsumed. That is the whole guard against
    an accidental increment: there is no render, no GET and no status change
    that reaches this function, and the one control that does is a POST a
    reader has to press.
    """
    interaction = await get_interaction(session, user_id=user_id, work_id=work_id)
    if interaction is None or interaction.removed_at is not None:
        raise InteractionNotFoundError("no library entry for that work")

    if interaction.status != STATUS_COMPLETED:
        raise ReconsumptionNotAvailableError(
            "only a completed work can record another completion"
        )

    # Two events, in this order, inside one request. `_now()` keeps them
    # apart -- which is the whole reason the event clock is not the wall
    # clock; a tie here would read as "completed, then started again".
    _apply_status(interaction, session, STATUS_IN_PROGRESS)
    _apply_status(interaction, session, STATUS_COMPLETED)

    await session.flush()
    return interaction


async def undo_reconsumption(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID
) -> UserContentInteraction:
    """Take back one recorded completion, exactly as it was recorded.

    Phase 1AB. Counting up was deliberate but one-way: a reader who pressed
    "Watch again" once too often had no way to say so, and the count is
    supposed to be what they *chose* to record.

    This is the inverse of `record_reconsumption` and nothing more. It
    removes the trailing `completed -> in_progress -> completed` pair that
    the increment wrote, decrements both counters by one, and rolls
    `completed_at` back to the completion that now ends the trail:

        times_started    -= 1
        times_completed  -= 1
        completed_at      = the previous completion
        status            = completed, where it began

    **The floor is one.** A work that has been completed once has one reading
    to its name, and the correction being offered is "that extra cycle did
    not happen" -- never "I never read this". Zero and negative counts are
    refused, and this call never removes the work, never touches the rating
    or `rated_at`, and never writes a rating event.

    The events are deleted rather than annotated. An undo is the reader
    saying a cycle was never real, so the honest record is one that does not
    claim it happened -- and a `completion_undone` event type would mean the
    history fold, the preference layer and every later reader of the log had
    to learn a fourth verb to describe something that is simply absent. The
    log stays append-only for everything that *did* occur.

    If the trail does not end in a pair this can lift off cleanly, nothing is
    changed and `NoReconsumptionToUndoError` is raised. Guessing at which
    events to remove would be worse than refusing.
    """
    interaction = await get_interaction(session, user_id=user_id, work_id=work_id)
    if interaction is None or interaction.removed_at is not None:
        raise InteractionNotFoundError("no library entry for that work")

    if interaction.status != STATUS_COMPLETED:
        raise ReconsumptionNotAvailableError(
            "only a completed work can have a completion taken back"
        )

    # The floor. One completion is a reading, not a mistake.
    if interaction.times_completed <= 1:
        raise NoReconsumptionToUndoError(
            "this work has only one recorded completion"
        )

    status_events = list(
        (
            await session.execute(
                select(UserContentEvent)
                .where(
                    UserContentEvent.interaction_id == interaction.id,
                    UserContentEvent.event_type == EVENT_STATUS_CHANGED,
                )
                .order_by(UserContentEvent.occurred_at, UserContentEvent.id)
            )
        )
        .scalars()
        .all()
    )

    # The last two status transitions must be the cycle being undone.
    if len(status_events) < 2:
        raise NoReconsumptionToUndoError("no completed cycle to take back")
    finished, restarted = status_events[-1], status_events[-2]
    if (
        finished.status_after != STATUS_COMPLETED
        or restarted.status_after != STATUS_IN_PROGRESS
    ):
        raise NoReconsumptionToUndoError("the recorded history does not end in a cycle")

    earlier_completions = [
        event
        for event in status_events[:-1]
        if event.status_after == STATUS_COMPLETED
    ]
    if not earlier_completions:
        raise NoReconsumptionToUndoError("no earlier completion to fall back to")

    await session.delete(finished)
    await session.delete(restarted)

    interaction.times_completed -= 1
    interaction.times_started -= 1
    # The work is still completed; it was completed earlier than the row
    # currently claims.
    interaction.completed_at = earlier_completions[-1].occurred_at
    interaction.status = STATUS_COMPLETED

    await session.flush()
    return interaction


async def set_rating(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID, rating: int | None
) -> UserContentInteraction:
    """Set or clear the user's explicit rating.

    `None` clears it back to unrated, which is a different state from a low
    rating and is stored as such. The change is recorded either way, so the
    user's rating history -- and therefore their own distribution -- stays
    reconstructable.
    """
    if rating is not None and not (RATING_MIN <= rating <= RATING_MAX):
        raise InvalidRatingError(f"rating must be between {RATING_MIN} and {RATING_MAX}")

    interaction = await get_interaction(session, user_id=user_id, work_id=work_id)
    if interaction is None or interaction.removed_at is not None:
        raise InteractionNotFoundError("no library entry for that work")

    before = interaction.rating
    interaction.rating = rating
    interaction.rated_at = _now() if rating is not None else None

    _record(
        interaction,
        session,
        event_type=EVENT_RATING_CHANGED,
        rating_before=before,
        rating_after=rating,
        status_before=interaction.status,
        status_after=interaction.status,
    )
    await session.flush()
    return interaction


async def list_events(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID, limit: int = 100
) -> list[UserContentEvent]:
    """This user's recorded history for one work, oldest first."""
    interaction = await get_interaction(session, user_id=user_id, work_id=work_id)
    if interaction is None:
        raise InteractionNotFoundError("no library entry for that work")

    result = await session.execute(
        select(UserContentEvent)
        .where(UserContentEvent.interaction_id == interaction.id)
        .order_by(UserContentEvent.occurred_at, UserContentEvent.id)
        .limit(limit)
    )
    return list(result.scalars().all())
