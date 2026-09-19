"""The authenticated user's library.

Addressed by canonical `work_id` throughout -- "is Frankenstein in my
library?" is the natural question, and it means interaction ids never have
to travel over the wire.

Every route derives its user from `get_current_user`. There is no path,
query or body parameter anywhere in this module that names a user, so one
user cannot address another's data by editing a request.

A missing entry and someone else's entry both return 404. That is
deliberate: a 403 would confirm the row exists.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models import User
from app.schemas.library import (
    EventRead,
    LibraryAdd,
    LibraryHistory,
    LibraryPage,
    LibraryStatuses,
    LibrarySummary,
    LibraryUpdate,
)
from app.schemas.product import WorkPresentation
from app.services import discovery_service, library_product, library_service, product_service

router = APIRouter(prefix="/library")

_NOT_IN_LIBRARY = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="no library entry for that work"
)


async def _present(db: AsyncSession, interaction) -> WorkPresentation:
    """Shape one library entry for the client.

    The same `WorkPresentation` the catalogue returns, so a work looks
    identical wherever it appears and a client needs one renderer. `work` is
    canonical and shared; `user_state` is this caller's alone.
    """
    projected = await product_service.product_works_for(db, [interaction.work])
    return WorkPresentation(
        work=projected[interaction.work_id],
        user_state=product_service.build_user_state(interaction),
    )


async def _present_many(db: AsyncSession, interactions: list) -> list[WorkPresentation]:
    """Shape a whole library in a fixed number of queries, never one per row."""
    projected = await product_service.product_works_for(
        db, [interaction.work for interaction in interactions]
    )
    return [
        WorkPresentation(
            work=projected[interaction.work_id],
            user_state=product_service.build_user_state(interaction),
        )
        for interaction in interactions
    ]


@router.get("/statuses", response_model=LibraryStatuses)
async def read_statuses() -> LibraryStatuses:
    """The controlled vocabulary and rating scale, so clients don't hardcode them."""
    return LibraryStatuses()


@router.get("/summary", response_model=LibrarySummary)
async def read_library_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LibrarySummary:
    """How much is in each part of this user's library.

    So a tabbed library can label every tab without fetching every tab --
    which is the alternative, and is downloading the whole library to count
    it. Plain counts, this user's only, and removed entries counted
    separately rather than folded into the total.

    Declared before `/{work_id}` so it is not read as a work id.
    """
    return library_product.build_summary(
        await library_service.status_counts(db, user_id=user.id),
        removed=await library_service.count_removed(db, user_id=user.id),
        rated=await library_service.count_rated(db, user_id=user.id),
    )


@router.get("", response_model=LibraryPage)
async def read_library(
    status_filter: str | None = Query(
        default=None, alias="status", description="One of the library statuses."
    ),
    domain: str | None = Query(default=None, description="Filter by domain slug"),
    include_removed: bool = Query(
        default=False,
        description="Soft-removed entries are excluded unless this is set.",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=discovery_service.DEFAULT_PAGE_SIZE,
        ge=1,
        le=discovery_service.MAX_PAGE_SIZE,
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LibraryPage:
    """This user's library. Never the ingestion corpus.

    Most recently touched first, so "what was I doing" is the first thing a
    page answers. Filtering is server-side: a library is small today and
    downloading all of it to pick out the completed ones stops working at the
    first heavy reader.

    **Removed entries are excluded by default.** Removal is soft -- the row,
    its ratings and its history survive -- but a tidied shelf is not a shelf
    item, and a removed work must never appear as though it were still held.
    """
    limit, offset = discovery_service.page_bounds(page, page_size)
    try:
        interactions = await library_service.list_library(
            db,
            user_id=user.id,
            status=status_filter,
            domain_slug=domain,
            include_removed=include_removed,
            limit=limit,
            offset=offset,
        )
        total = await library_service.count_library(
            db,
            user_id=user.id,
            status=status_filter,
            domain_slug=domain,
            include_removed=include_removed,
        )
    except library_service.InvalidStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None

    return LibraryPage(
        items=await _present_many(db, interactions),
        total=total,
        page=page,
        page_size=limit,
    )


@router.post("", response_model=WorkPresentation, status_code=status.HTTP_201_CREATED)
async def add_library_entry(
    payload: LibraryAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkPresentation:
    """Reference a canonical work from this user's library. Never copies it."""
    try:
        interaction = await library_service.add_to_library(
            db, user_id=user.id, work_id=payload.work_id, status=payload.status
        )
    except library_service.WorkNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="work not found"
        ) from None
    except library_service.AlreadyInLibraryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except library_service.InvalidStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None

    await db.commit()
    interaction = await library_service.get_interaction(
        db, user_id=user.id, work_id=payload.work_id
    )
    return await _present(db, interaction)


@router.get("/{work_id}", response_model=WorkPresentation)
async def read_library_entry(
    work_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkPresentation:
    interaction = await library_service.get_interaction(db, user_id=user.id, work_id=work_id)
    if interaction is None:
        raise _NOT_IN_LIBRARY
    return await _present(db, interaction)


@router.patch("/{work_id}", response_model=WorkPresentation)
async def update_library_entry(
    work_id: uuid.UUID,
    payload: LibraryUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkPresentation:
    """Update status and/or rating.

    The two are applied independently and neither is derived from the other:
    marking something completed leaves it unrated, and rating something
    leaves its status alone.
    """
    try:
        interaction = None
        if payload.status is not None:
            interaction = await library_service.set_status(
                db, user_id=user.id, work_id=work_id, status=payload.status
            )
        if payload.rating is not None or payload.rating_set:
            interaction = await library_service.set_rating(
                db, user_id=user.id, work_id=work_id, rating=payload.rating
            )
        if interaction is None:
            interaction = await library_service.get_interaction(
                db, user_id=user.id, work_id=work_id
            )
            if interaction is None or interaction.removed_at is not None:
                raise _NOT_IN_LIBRARY
    except library_service.InteractionNotFoundError:
        raise _NOT_IN_LIBRARY from None
    except (library_service.InvalidStatusError, library_service.InvalidRatingError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None

    await db.commit()
    interaction = await library_service.get_interaction(db, user_id=user.id, work_id=work_id)
    return await _present(db, interaction)


@router.delete("/{work_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_library_entry(
    work_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove from the library.

    Soft: the interaction and its history survive, because a work someone
    completed and rated 9 is preference evidence whether or not they still
    want it on the shelf.
    """
    try:
        await library_service.remove_from_library(db, user_id=user.id, work_id=work_id)
    except library_service.InteractionNotFoundError:
        raise _NOT_IN_LIBRARY from None

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{work_id}/history", response_model=LibraryHistory)
async def read_library_entry_history(
    work_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LibraryHistory:
    """What happened with this work, as a reader would describe it.

    A projection of the stored event log, not the log itself: no event ids,
    no internal event types, no before/after status pairs. `added` after a
    removal becomes `returned`, and a start after a completion becomes
    `restarted` -- which is how reconsumption becomes legible without anyone
    having to learn the word.

    The raw log stays at `/{work_id}/events`, which is a development surface.
    """
    interaction = await library_service.get_interaction(
        db, user_id=user.id, work_id=work_id
    )
    if interaction is None:
        raise _NOT_IN_LIBRARY

    events = await library_service.list_events(
        db, user_id=user.id, work_id=work_id, limit=500
    )
    return library_product.build_history(interaction, events)


@router.get("/{work_id}/events", response_model=list[EventRead])
async def read_library_entry_events(
    work_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EventRead]:
    """The raw stored event log for one entry, oldest first.

    A development surface. `/{work_id}/history` is what a product renders.
    """
    try:
        events = await library_service.list_events(
            db, user_id=user.id, work_id=work_id, limit=limit
        )
    except library_service.InteractionNotFoundError:
        raise _NOT_IN_LIBRARY from None
    return [EventRead.model_validate(event) for event in events]
