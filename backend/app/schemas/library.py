"""Request/response shapes for accounts and the user library.

These deliberately expose *presentation* metadata about canonical content --
title, domain, source -- and never content units, text or embeddings. The
raw corpus stays internal to the system; a library entry is a reference plus
the user's own state.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import RATING_MAX, RATING_MIN, STATUSES
from app.schemas.product import WorkPresentation


# Not an RFC 5322 validator, and not pretending to be one. Nothing in this
# phase sends email -- no verification, no reset -- so the address only has
# to be a plausible, normalizable identifier. `auth_service` enforces the
# same rule, and is the authority.
EmailField = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)]


class UserRegister(BaseModel):
    email: EmailField
    # Length is enforced again in the service, which is the authority; this
    # just fails obviously wrong input before it reaches a KDF.
    password: str = Field(min_length=10, max_length=256)
    display_name: str | None = Field(default=None, max_length=128)


class UserLogin(BaseModel):
    email: EmailField
    password: str = Field(min_length=1, max_length=256)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime


class SessionRead(BaseModel):
    """Returned once, at login. The token is never retrievable again."""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserRead


class LibraryAdd(BaseModel):
    work_id: uuid.UUID
    status: str = Field(default="planned")


class LibraryUpdate(BaseModel):
    """A partial update. Omitted fields are left alone.

    `rating` is nullable *and* optional, and those mean different things:
    omitting it changes nothing, sending null clears the rating back to
    unrated. `rating_set` distinguishes the two.
    """

    status: str | None = None
    rating: int | None = Field(default=None, ge=RATING_MIN, le=RATING_MAX)
    rating_set: bool = Field(
        default=False,
        description="Send true with rating=null to explicitly clear the rating",
    )


class LibraryPage(BaseModel):
    """One page of a reader's library.

    Phase 1Z. The same envelope Phase 1Y gave discovery, for the same reason:
    a library is small today and an endpoint whose contract is "everything"
    has to be redesigned the first time one is not. `total` counts what
    matched the filter, not what is on this page.
    """

    items: list[WorkPresentation] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class LibrarySummary(BaseModel):
    """How much is in each part of a reader's library.

    Exists so a tabbed library can label its tabs without fetching every tab.
    Counts only, and only this reader's -- there is no aggregate across users
    here and nothing that could be read as a score.

    `by_status` is keyed by the same controlled vocabulary `LibraryStatuses`
    publishes, and every status appears even at zero: a tab that vanishes
    when it empties is a worse tab than one that says nothing is in it.
    """

    # Currently in the library: `removed` is excluded, because a tidied shelf
    # is not a shelf item.
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    # Soft-removed entries, whose history and ratings are still kept. Counted
    # separately so a client can offer them deliberately rather than mixing
    # them into the active library.
    removed: int = 0
    rated: int = 0


# What a reader sees in a work's history. A **product** vocabulary, derived
# from the stored events rather than echoing them: `status_changed` is an
# implementation detail and `added` after a removal is really a return.
HISTORY_ADDED = "added"
HISTORY_RETURNED = "returned"
HISTORY_STARTED = "started"
HISTORY_RESTARTED = "restarted"
HISTORY_COMPLETED = "completed"
HISTORY_PAUSED = "paused"
HISTORY_ABANDONED = "abandoned"
HISTORY_PLANNED = "planned"
HISTORY_RATED = "rated"
HISTORY_RATING_CLEARED = "rating_cleared"
HISTORY_REMOVED = "removed"

HISTORY_KINDS = (
    HISTORY_ADDED,
    HISTORY_RETURNED,
    HISTORY_STARTED,
    HISTORY_RESTARTED,
    HISTORY_COMPLETED,
    HISTORY_PAUSED,
    HISTORY_ABANDONED,
    HISTORY_PLANNED,
    HISTORY_RATED,
    HISTORY_RATING_CLEARED,
    HISTORY_REMOVED,
)


class HistoryEntry(BaseModel):
    """One thing that happened, as a reader would describe it.

    No event id, no internal event type, no before/after status pair. A
    renderer turns `kind` into a sentence; this contract deliberately sends
    no prose, the same rule the taste dashboard follows.
    """

    kind: str
    occurred_at: datetime
    # Present only on `rated`. The rating that was given, never a preference
    # value of any kind.
    rating: int | None = None


class LibraryHistory(BaseModel):
    """A work's history for one reader, oldest first.

    Compact on purpose. The stored event log is richer and stays where it is:
    `/library/{work_id}/events` is the development surface for it, exactly as
    `/works/{id}/internal` is for the catalogue.
    """

    entries: list[HistoryEntry] = Field(default_factory=list)
    # Carried from the interaction rather than recounted from the entries, so
    # the figure a reader sees is the one the preference engine reads.
    times_started: int = 0
    times_completed: int = 0
    current_status: str
    rating: int | None = None
    in_library: bool = True


class EventRead(BaseModel):
    """The raw stored event.

    A **development** surface, like `/works/{id}/internal`. It carries the
    event's own id and the internal event vocabulary, neither of which belongs
    in front of a reader -- `LibraryHistory` is what a product renders.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    status_before: str | None
    status_after: str | None
    rating_before: int | None
    rating_after: int | None
    occurred_at: datetime


class LibraryStatuses(BaseModel):
    """The controlled vocabulary, so a client never hardcodes it."""

    statuses: list[str] = list(STATUSES)
    rating_min: int = RATING_MIN
    rating_max: int = RATING_MAX
