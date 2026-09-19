"""Projecting a reader's library onto what the product shows.

Phase 1Z. The same split `product.py` and `dashboard_product.py` already
use: `library_service` decides what is stored and what is true, and this
module decides what a reader is shown. It computes nothing and stores
nothing.

---

Why the stored events are not the history

`UserContentEvent` is an append-only record built for the taste layer. It
speaks in `added` / `status_changed` / `rating_changed` / `removed`, carries
its own primary key, and describes a transition as a before/after pair. All
of that is right for storage and wrong in front of a reader:

    status_changed, on_hold -> in_progress     what is stored
    Started again                              what happened

So this module folds the log into a small closed vocabulary of things that
*happened*, and drops the row ids entirely. Two of them need the fold rather
than a lookup table:

    returned     an `added` event is only a beginning the first time. After a
                 removal it is a return, and the difference matters because
                 the rating from before is still there.

    restarted    a start is a restart once the work has been completed at
                 least once before that point in the log. This is what makes
                 reconsumption legible without a reader learning the word
                 "reconsumption" -- and it is derived by counting completions
                 *seen so far*, not from the row's final `times_completed`,
                 so an old start is never relabelled by a later finish.

`planned` transitions are kept but are rarely interesting; a renderer can
drop them. Nothing here is a claim about preference: a completion is not a
rating and an abandonment is not a negative one, exactly as at write time.
"""

from app.models import (
    EVENT_ADDED,
    EVENT_RATING_CHANGED,
    EVENT_REMOVED,
    EVENT_STATUS_CHANGED,
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_ON_HOLD,
    STATUS_PLANNED,
    UserContentEvent,
    UserContentInteraction,
)
from app.schemas.library import (
    HISTORY_ABANDONED,
    HISTORY_ADDED,
    HISTORY_COMPLETED,
    HISTORY_PAUSED,
    HISTORY_PLANNED,
    HISTORY_RATED,
    HISTORY_RATING_CLEARED,
    HISTORY_REMOVED,
    HISTORY_RESTARTED,
    HISTORY_RETURNED,
    HISTORY_STARTED,
    HistoryEntry,
    LibraryHistory,
    LibrarySummary,
)

# Status transitions that have a name a reader would use. `in_progress` is
# absent because it depends on what came before it; see `_history_kind`.
_STATUS_KINDS = {
    STATUS_COMPLETED: HISTORY_COMPLETED,
    STATUS_ON_HOLD: HISTORY_PAUSED,
    STATUS_ABANDONED: HISTORY_ABANDONED,
    STATUS_PLANNED: HISTORY_PLANNED,
}


def _history_kind(
    event: UserContentEvent, *, completions_so_far: int, removed_before: bool
) -> str | None:
    """One stored event as the thing a reader would say happened."""
    if event.event_type == EVENT_ADDED:
        return HISTORY_RETURNED if removed_before else HISTORY_ADDED

    if event.event_type == EVENT_REMOVED:
        return HISTORY_REMOVED

    if event.event_type == EVENT_RATING_CHANGED:
        return HISTORY_RATED if event.rating_after is not None else HISTORY_RATING_CLEARED

    if event.event_type == EVENT_STATUS_CHANGED:
        if event.status_after == STATUS_IN_PROGRESS:
            # A start is a restart only once something has already been
            # finished, and only counting what had been finished *by then*.
            return HISTORY_RESTARTED if completions_so_far > 0 else HISTORY_STARTED
        return _STATUS_KINDS.get(event.status_after or "")

    # An event type this build does not recognise is dropped rather than
    # guessed at. Silence is better than a wrong sentence about someone's
    # own history.
    return None


def build_history(
    interaction: UserContentInteraction, events: list[UserContentEvent]
) -> LibraryHistory:
    """A work's history for one reader, oldest first.

    `events` is expected in the order `library_service.list_events` returns
    them -- oldest first -- because the fold depends on what had happened by
    each point.
    """
    entries: list[HistoryEntry] = []
    completions = 0
    removed = False

    for event in events:
        kind = _history_kind(
            event, completions_so_far=completions, removed_before=removed
        )
        if kind is None:
            continue

        entries.append(
            HistoryEntry(
                kind=kind,
                occurred_at=event.occurred_at,
                # Only a rating carries a number, and it is the rating the
                # reader gave -- never anything the preference engine derived.
                rating=event.rating_after if kind == HISTORY_RATED else None,
            )
        )

        if kind == HISTORY_COMPLETED:
            completions += 1
        elif kind == HISTORY_REMOVED:
            removed = True
        elif kind in (HISTORY_ADDED, HISTORY_RETURNED):
            removed = False

    return LibraryHistory(
        entries=entries,
        # From the row, not recounted from the fold: this is the figure the
        # preference engine reads, and the two must not be able to disagree.
        times_started=interaction.times_started,
        times_completed=interaction.times_completed,
        current_status=interaction.status,
        rating=interaction.rating,
        in_library=interaction.removed_at is None,
    )


def build_summary(
    counts: dict[str, int], *, removed: int, rated: int
) -> LibrarySummary:
    """Tab counts for a library. Plain integers, and nothing else.

    `total` deliberately excludes removed entries: the primary library is
    what is currently in it, and a tidied shelf is not a shelf item.
    """
    return LibrarySummary(
        total=sum(counts.values()),
        by_status=dict(counts),
        removed=removed,
        rated=rated,
    )


__all__ = ["build_history", "build_summary"]
