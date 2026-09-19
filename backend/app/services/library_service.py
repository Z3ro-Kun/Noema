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
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        .order_by(UserContentInteraction.updated_at.desc())
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
) -> None:
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
    _record(
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
