"""The user/canonical-content boundary, inside rolled-back transactions.

The questions these answer are the ones the phase exists for: does a user's
library reference shared content rather than copying it, and does the schema
keep apart the signals a taste layer will later need to tell apart?

Fixture-scoped throughout. Each test builds a tiny work of its own -- never
the production corpus -- following the Phase 1J principle.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    Container,
    ContentUnit,
    User,
    UserContentEvent,
    UserContentInteraction,
    Work,
)
from app.services import auth_service, library_service
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
PASSWORD = "a-sufficiently-long-password"


async def make_user(session: AsyncSession, email: str) -> User:
    return await auth_service.register_user(session, email=email, password=PASSWORD)


async def make_work(session: AsyncSession, source_ref: str = "library-test-work") -> Work:
    """One small canonical work, scoped to this test."""
    result = await ingest_source_work(
        session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="The Lantern Keeper",
            source_ref=source_ref,
            author="A Test Author",
        ).load(),
    )
    return await session.get(Work, result.work_id)


# --- canonical content stays shared --------------------------------------


async def test_three_users_share_one_canonical_work(db_session: AsyncSession) -> None:
    """The whole point: Frankenstein is one row, with three opinions attached."""
    work = await make_work(db_session)
    alice = await make_user(db_session, "alice@example.test")
    bob = await make_user(db_session, "bob@example.test")
    carol = await make_user(db_session, "carol@example.test")

    await library_service.add_to_library(db_session, user_id=alice.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=alice.id, work_id=work.id, status=STATUS_COMPLETED
    )
    await library_service.set_rating(db_session, user_id=alice.id, work_id=work.id, rating=9)

    await library_service.add_to_library(db_session, user_id=bob.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=bob.id, work_id=work.id, status=STATUS_IN_PROGRESS
    )
    await library_service.set_rating(db_session, user_id=bob.id, work_id=work.id, rating=8)

    await library_service.add_to_library(db_session, user_id=carol.id, work_id=work.id)

    # One work, one set of containers, one set of content units -- no copies.
    works = await db_session.execute(
        select(func.count()).select_from(Work).where(Work.id == work.id)
    )
    assert works.scalar_one() == 1

    interactions = (
        (
            await db_session.execute(
                select(UserContentInteraction).where(
                    UserContentInteraction.work_id == work.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(interactions) == 3
    assert {i.work_id for i in interactions} == {work.id}
    assert sorted(
        (i.status, i.rating) for i in interactions
    ) == [
        (STATUS_COMPLETED, 9),
        (STATUS_IN_PROGRESS, 8),
        (STATUS_PLANNED, None),
    ]


async def test_adding_to_a_library_creates_no_content(db_session: AsyncSession) -> None:
    """A library entry is a reference. It must not duplicate a single row of content."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")

    async def counts():
        containers = await db_session.execute(
            select(func.count()).select_from(Container).where(Container.work_id == work.id)
        )
        units = await db_session.execute(
            select(func.count())
            .select_from(ContentUnit)
            .join(Container, ContentUnit.container_id == Container.id)
            .where(Container.work_id == work.id)
        )
        return containers.scalar_one(), units.scalar_one()

    before = await counts()
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_COMPLETED
    )
    await library_service.set_rating(db_session, user_id=user.id, work_id=work.id, rating=7)

    assert await counts() == before
    assert before[1] > 0  # the work really does have content to have copied


async def test_a_user_cannot_hold_two_entries_for_one_work(db_session: AsyncSession) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    with pytest.raises(library_service.AlreadyInLibraryError):
        await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)


async def test_adding_an_unknown_work_is_refused(db_session: AsyncSession) -> None:
    import uuid

    user = await make_user(db_session, "reader@example.test")

    with pytest.raises(library_service.WorkNotFoundError):
        await library_service.add_to_library(
            db_session, user_id=user.id, work_id=uuid.uuid4()
        )


# --- the signals the taste layer must be able to tell apart ---------------


async def test_library_addition_alone_records_no_engagement(
    db_session: AsyncSession,
) -> None:
    """Added but never opened: the weakest signal, and distinguishable as such."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")

    interaction = await library_service.add_to_library(
        db_session, user_id=user.id, work_id=work.id
    )

    assert interaction.added_at is not None
    assert interaction.status == STATUS_PLANNED
    assert interaction.started_at is None  # no exposure
    assert interaction.completed_at is None
    assert interaction.rating is None
    assert interaction.times_started == 0


async def test_starting_records_exposure_separately_from_addition(
    db_session: AsyncSession,
) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    interaction = await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_IN_PROGRESS
    )

    assert interaction.started_at is not None
    assert interaction.added_at is not None
    assert interaction.times_started == 1
    assert interaction.completed_at is None


async def test_completion_is_not_stored_as_a_rating(db_session: AsyncSession) -> None:
    """`consumed X` is not `prefers X`. Finishing must leave a work unrated."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    interaction = await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_COMPLETED
    )

    assert interaction.status == STATUS_COMPLETED
    assert interaction.completed_at is not None
    assert interaction.times_completed == 1
    # The load-bearing assertion of this phase.
    assert interaction.rating is None
    assert interaction.rated_at is None


async def test_abandonment_is_its_own_signal_and_not_a_low_rating(
    db_session: AsyncSession,
) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_IN_PROGRESS
    )

    interaction = await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_ABANDONED
    )

    assert interaction.status == STATUS_ABANDONED
    assert interaction.abandoned_at is not None
    # No score is assigned. Abandonment stays ambiguous evidence.
    assert interaction.rating is None
    assert interaction.completed_at is None
    # And the exposure it did get is still recorded.
    assert interaction.started_at is not None


async def test_on_hold_is_distinct_from_abandoned(db_session: AsyncSession) -> None:
    """Pausing must not pollute the abandonment signal."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_IN_PROGRESS
    )

    interaction = await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_ON_HOLD
    )

    assert interaction.status == STATUS_ON_HOLD
    assert interaction.abandoned_at is None


async def test_unrated_completed_and_low_rated_are_three_different_states(
    db_session: AsyncSession,
) -> None:
    """None of these may collapse into one another."""
    user = await make_user(db_session, "reader@example.test")
    unrated = await make_work(db_session, "lib-unrated")
    low = await make_work(db_session, "lib-low")
    high = await make_work(db_session, "lib-high")

    for work in (unrated, low, high):
        await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
        await library_service.set_status(
            db_session, user_id=user.id, work_id=work.id, status=STATUS_COMPLETED
        )
    await library_service.set_rating(db_session, user_id=user.id, work_id=low.id, rating=2)
    await library_service.set_rating(db_session, user_id=user.id, work_id=high.id, rating=10)

    states = {}
    for work in (unrated, low, high):
        row = await library_service.get_interaction(
            db_session, user_id=user.id, work_id=work.id
        )
        states[work.id] = (row.status, row.rating)

    assert states[unrated.id] == (STATUS_COMPLETED, None)
    assert states[low.id] == (STATUS_COMPLETED, 2)
    assert states[high.id] == (STATUS_COMPLETED, 10)


async def test_rating_does_not_change_status(db_session: AsyncSession) -> None:
    """The two axes are independent in both directions."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    interaction = await library_service.set_rating(
        db_session, user_id=user.id, work_id=work.id, rating=9
    )

    assert interaction.rating == 9
    # Rating something you have not started does not mean you started it.
    assert interaction.status == STATUS_PLANNED
    assert interaction.started_at is None


async def test_a_rating_can_be_cleared_back_to_unrated(db_session: AsyncSession) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
    await library_service.set_rating(db_session, user_id=user.id, work_id=work.id, rating=5)

    interaction = await library_service.set_rating(
        db_session, user_id=user.id, work_id=work.id, rating=None
    )

    assert interaction.rating is None
    assert interaction.rated_at is None


@pytest.mark.parametrize("rating", [0, 11, -1, 100])
async def test_ratings_outside_the_scale_are_refused(
    db_session: AsyncSession, rating: int
) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    with pytest.raises(library_service.InvalidRatingError):
        await library_service.set_rating(
            db_session, user_id=user.id, work_id=work.id, rating=rating
        )


async def test_an_unknown_status_is_refused(db_session: AsyncSession) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    with pytest.raises(library_service.InvalidStatusError):
        await library_service.set_status(
            db_session, user_id=user.id, work_id=work.id, status="devoured"
        )


# --- reconsumption -------------------------------------------------------


async def test_rereading_counts_without_erasing_the_first_reading(
    db_session: AsyncSession,
) -> None:
    """Repeated engagement is behavioural preference evidence; it must survive."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    for _ in range(3):
        await library_service.set_status(
            db_session, user_id=user.id, work_id=work.id, status=STATUS_IN_PROGRESS
        )
        await library_service.set_status(
            db_session, user_id=user.id, work_id=work.id, status=STATUS_COMPLETED
        )

    interaction = await library_service.get_interaction(
        db_session, user_id=user.id, work_id=work.id
    )
    assert interaction.times_started == 3
    assert interaction.times_completed == 3
    assert interaction.status == STATUS_COMPLETED

    # And a one-time reader of the same work is distinguishable from them.
    other = await make_user(db_session, "other@example.test")
    await library_service.add_to_library(db_session, user_id=other.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=other.id, work_id=work.id, status=STATUS_COMPLETED
    )
    once = await library_service.get_interaction(
        db_session, user_id=other.id, work_id=work.id
    )
    assert once.times_completed == 1


async def test_changing_a_rating_preserves_the_previous_one_in_history(
    db_session: AsyncSession,
) -> None:
    """The user's own rating distribution stays reconstructable over time."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    for rating in (4, 7, 9):
        await library_service.set_rating(
            db_session, user_id=user.id, work_id=work.id, rating=rating
        )

    events = await library_service.list_events(db_session, user_id=user.id, work_id=work.id)
    rating_events = [e for e in events if e.event_type == EVENT_RATING_CHANGED]

    assert [(e.rating_before, e.rating_after) for e in rating_events] == [
        (None, 4),
        (4, 7),
        (7, 9),
    ]


async def test_every_transition_is_recorded_in_order(db_session: AsyncSession) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")

    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_IN_PROGRESS
    )
    await library_service.set_rating(db_session, user_id=user.id, work_id=work.id, rating=8)
    await library_service.remove_from_library(db_session, user_id=user.id, work_id=work.id)

    events = await library_service.list_events(db_session, user_id=user.id, work_id=work.id)
    assert [e.event_type for e in events] == [
        EVENT_ADDED,
        EVENT_STATUS_CHANGED,
        EVENT_RATING_CHANGED,
        EVENT_REMOVED,
    ]


# --- removal is soft -----------------------------------------------------


async def test_removal_leaves_the_library_but_keeps_the_evidence(
    db_session: AsyncSession,
) -> None:
    """A 9/10 someone tidied away is still the strongest signal they gave."""
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
    await library_service.set_status(
        db_session, user_id=user.id, work_id=work.id, status=STATUS_COMPLETED
    )
    await library_service.set_rating(db_session, user_id=user.id, work_id=work.id, rating=9)

    await library_service.remove_from_library(db_session, user_id=user.id, work_id=work.id)

    assert await library_service.list_library(db_session, user_id=user.id) == []
    kept = await library_service.get_interaction(db_session, user_id=user.id, work_id=work.id)
    assert kept is not None
    assert kept.in_library is False
    assert kept.rating == 9
    assert kept.times_completed == 1


async def test_readding_revives_the_original_entry_with_its_history(
    db_session: AsyncSession,
) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    first = await library_service.add_to_library(
        db_session, user_id=user.id, work_id=work.id
    )
    await library_service.set_rating(db_session, user_id=user.id, work_id=work.id, rating=6)
    await library_service.remove_from_library(db_session, user_id=user.id, work_id=work.id)

    revived = await library_service.add_to_library(
        db_session, user_id=user.id, work_id=work.id
    )

    assert revived.id == first.id  # same row, not a second one
    assert revived.rating == 6
    assert revived.in_library is True

    rows = await db_session.execute(
        select(func.count())
        .select_from(UserContentInteraction)
        .where(
            UserContentInteraction.user_id == user.id,
            UserContentInteraction.work_id == work.id,
        )
    )
    assert rows.scalar_one() == 1


async def test_removing_something_not_in_the_library_is_refused(
    db_session: AsyncSession,
) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")

    with pytest.raises(library_service.InteractionNotFoundError):
        await library_service.remove_from_library(
            db_session, user_id=user.id, work_id=work.id
        )


# --- isolation between users ---------------------------------------------


async def test_one_user_cannot_read_anothers_interaction(db_session: AsyncSession) -> None:
    work = await make_work(db_session)
    alice = await make_user(db_session, "alice@example.test")
    bob = await make_user(db_session, "bob@example.test")
    await library_service.add_to_library(db_session, user_id=alice.id, work_id=work.id)

    assert (
        await library_service.get_interaction(db_session, user_id=bob.id, work_id=work.id)
    ) is None
    assert await library_service.list_library(db_session, user_id=bob.id) == []


async def test_one_user_cannot_modify_anothers_interaction(db_session: AsyncSession) -> None:
    work = await make_work(db_session)
    alice = await make_user(db_session, "alice@example.test")
    bob = await make_user(db_session, "bob@example.test")
    await library_service.add_to_library(db_session, user_id=alice.id, work_id=work.id)
    await library_service.set_rating(db_session, user_id=alice.id, work_id=work.id, rating=9)

    for call in (
        library_service.set_status(
            db_session, user_id=bob.id, work_id=work.id, status=STATUS_COMPLETED
        ),
        library_service.set_rating(db_session, user_id=bob.id, work_id=work.id, rating=1),
        library_service.remove_from_library(db_session, user_id=bob.id, work_id=work.id),
    ):
        with pytest.raises(library_service.InteractionNotFoundError):
            await call

    untouched = await library_service.get_interaction(
        db_session, user_id=alice.id, work_id=work.id
    )
    assert untouched.rating == 9
    assert untouched.status == STATUS_PLANNED
    assert untouched.removed_at is None


async def test_one_user_cannot_read_anothers_event_history(
    db_session: AsyncSession,
) -> None:
    work = await make_work(db_session)
    alice = await make_user(db_session, "alice@example.test")
    bob = await make_user(db_session, "bob@example.test")
    await library_service.add_to_library(db_session, user_id=alice.id, work_id=work.id)

    with pytest.raises(library_service.InteractionNotFoundError):
        await library_service.list_events(db_session, user_id=bob.id, work_id=work.id)


async def test_deleting_a_user_removes_their_library_but_not_the_content(
    db_session: AsyncSession,
) -> None:
    work = await make_work(db_session)
    alice = await make_user(db_session, "alice@example.test")
    bob = await make_user(db_session, "bob@example.test")
    await library_service.add_to_library(db_session, user_id=alice.id, work_id=work.id)
    await library_service.add_to_library(db_session, user_id=bob.id, work_id=work.id)

    await db_session.delete(alice)
    await db_session.flush()

    # Alice's rows are gone, Bob's survive, and the canonical work is untouched.
    remaining = (
        (
            await db_session.execute(
                select(UserContentInteraction).where(
                    UserContentInteraction.work_id == work.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [i.user_id for i in remaining] == [bob.id]
    assert await db_session.get(Work, work.id) is not None


async def test_deleting_an_interaction_cascades_only_to_its_events(
    db_session: AsyncSession,
) -> None:
    work = await make_work(db_session)
    user = await make_user(db_session, "reader@example.test")
    interaction = await library_service.add_to_library(
        db_session, user_id=user.id, work_id=work.id
    )

    await db_session.delete(interaction)
    await db_session.flush()

    events = await db_session.execute(
        select(func.count())
        .select_from(UserContentEvent)
        .where(UserContentEvent.interaction_id == interaction.id)
    )
    assert events.scalar_one() == 0
    assert await db_session.get(Work, work.id) is not None


# --- listing and filtering -----------------------------------------------


async def test_library_can_be_filtered_by_status(db_session: AsyncSession) -> None:
    user = await make_user(db_session, "reader@example.test")
    reading = await make_work(db_session, "lib-filter-a")
    planned = await make_work(db_session, "lib-filter-b")

    await library_service.add_to_library(db_session, user_id=user.id, work_id=reading.id)
    await library_service.set_status(
        db_session, user_id=user.id, work_id=reading.id, status=STATUS_IN_PROGRESS
    )
    await library_service.add_to_library(db_session, user_id=user.id, work_id=planned.id)

    in_progress = await library_service.list_library(
        db_session, user_id=user.id, status=STATUS_IN_PROGRESS
    )
    assert [i.work_id for i in in_progress] == [reading.id]
    assert await library_service.count_library(db_session, user_id=user.id) == 2


async def test_library_can_be_filtered_by_domain(db_session: AsyncSession) -> None:
    user = await make_user(db_session, "reader@example.test")
    work = await make_work(db_session, "lib-domain")
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    assert len(
        await library_service.list_library(
            db_session, user_id=user.id, domain_slug="literature"
        )
    ) == 1
    assert (
        await library_service.list_library(db_session, user_id=user.id, domain_slug="anime")
        == []
    )
