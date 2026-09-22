"""The clock the event logs are stamped from.

`datetime.now()` reports the wall clock at whatever resolution the host
offers, and that resolution is coarse: on Windows it is between 0.5 ms and
15.6 ms depending on what else is running, so several writes made inside one
request -- or two requests made back to back -- routinely land on the *same*
instant.

That is fine for a timestamp read as "roughly when". It is not fine for the
two places that read one as "in what order":

    history ordering    events are returned `order_by(occurred_at, id)`, and
                        `id` is a random uuid4 -- so a tie does not resolve
                        into a sequence, it resolves into a shuffle.

    change detection    assigning a column the value it already holds reads
                        as *unchanged* to the ORM, which then lets a
                        server-side `onupdate` take over and expires the
                        attribute. On an async session, reading an expired
                        attribute attempts IO from a context that cannot
                        await it.

So the event logs take their timestamps from here instead. `now()` never
returns the same instant twice in a process: when the wall clock has not
moved, it advances by the smallest step PostgreSQL can store. The value
stays a true wall-clock reading to within one tick, and "later timestamp"
becomes a fact about order rather than a coincidence of resolution.

This is a per-process guarantee. Two processes stamping the same microsecond
are genuinely concurrent, and nothing here pretends to order them.
"""

import threading
from datetime import datetime, timedelta, timezone

# PostgreSQL stores `timestamptz` to the microsecond, so this is the
# smallest step that survives a round trip.
TICK = timedelta(microseconds=1)

_lock = threading.Lock()
_last: datetime | None = None


def now() -> datetime:
    """A UTC instant strictly later than every instant this process issued."""
    global _last
    with _lock:
        current = datetime.now(timezone.utc)
        if _last is not None and current <= _last:
            current = _last + TICK
        _last = current
        return current


__all__ = ["TICK", "now"]
