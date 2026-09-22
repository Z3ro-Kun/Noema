"""The clock the event logs are stamped from, and why it is not the wall clock.

Two product behaviours rest on event timestamps being *distinct*:

    history is a sequence      `order_by(occurred_at, id)` resolves a tie with
                               a random uuid4, so equal timestamps do not
                               order -- they shuffle.

    a re-answer is a change    assigning a column the value it already holds
                               reads as unchanged to the ORM, which hands the
                               write to the column's server-side `onupdate`
                               and leaves the attribute expired. Reading it
                               back on an async session is then IO from a
                               context that cannot await.

The wall clock does not provide distinctness. Its resolution on Windows sits
between 0.5 ms and 15.6 ms depending on what else on the machine has asked
for a finer timer, which is longer than the work between two writes in one
request -- and it varies from run to run, which is why the failures this file
covers were intermittent rather than absent.

So these tests run against a wall clock that has *stopped*: the worst case,
and the one that makes the guarantee unambiguous. If ordering survives a
clock that never moves, it survives every real clock.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core import clock


@pytest.fixture
def stopped_wall_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Freeze the reading `clock.now()` builds on, and reset its memory."""
    frozen = datetime.now(timezone.utc)

    class StoppedWallClock:
        @staticmethod
        def now(tz: timezone | None = None) -> datetime:
            return frozen

    monkeypatch.setattr(clock, "datetime", StoppedWallClock)
    monkeypatch.setattr(clock, "_last", None)
    return frozen


def test_the_event_clock_never_repeats_an_instant(stopped_wall_clock: datetime) -> None:
    """Even with the wall clock stopped, no two readings are equal."""
    stamps = [clock.now() for _ in range(1000)]

    assert len(set(stamps)) == len(stamps)
    assert stamps == sorted(stamps), "and they come out in issue order"


def test_the_event_clock_advances_by_the_smallest_storable_step(
    stopped_wall_clock: datetime,
) -> None:
    """A stopped wall clock costs one microsecond per reading, not more.

    Postgres stores `timestamptz` to the microsecond, so a smaller step would
    collapse on the round trip and a larger one would drift the timestamp
    away from the real instant faster than it has to.
    """
    first = clock.now()
    second = clock.now()

    assert first == stopped_wall_clock
    assert second - first == timedelta(microseconds=1)


def test_the_event_clock_follows_the_wall_clock_when_it_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step is a floor, not an offset: real time wins whenever it leads."""
    base = datetime.now(timezone.utc)
    readings = iter([base, base + timedelta(seconds=5)])

    class MovingWallClock:
        @staticmethod
        def now(tz: timezone | None = None) -> datetime:
            return next(readings)

    monkeypatch.setattr(clock, "datetime", MovingWallClock)
    monkeypatch.setattr(clock, "_last", None)

    assert clock.now() == base
    assert clock.now() == base + timedelta(seconds=5)


def test_the_event_clock_stays_a_true_reading(stopped_wall_clock: datetime) -> None:
    """A burst of events is still stamped with when it happened.

    The correction is a microsecond per event, so a whole request's worth of
    writes stays inside one tick of the clock it is correcting for. The
    timestamp remains an answer to "when", not a sequence number wearing a
    timestamp's clothes.
    """
    stamps = [clock.now() for _ in range(50)]

    assert stamps[-1] - stopped_wall_clock < timedelta(milliseconds=1)
