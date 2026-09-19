"""The evaluation library builds the behavioural patterns it documents.

These tests assert nothing about what the patterns *mean* -- no preference is
computed anywhere in this phase. They check that the fixtures actually
contain the evidence their documentation claims, so that when a preference
engine is eventually written, the cases it is judged against are real.

Everything runs inside a rolled-back transaction. Nothing reaches the
development corpus.

That rollback isolates what these tests **write**, not what they **read**.
A query here still sees every committed row in the development database, so
assertions about the dataset are scoped to the reserved e-mail domain rather
than counted over a whole table. Two of them once counted whole tables and
passed only because no real account had ever been created; the first real
sign-up broke them while the builder and its teardown were working perfectly.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Concept,
    User,
    UserContentEvent,
    UserContentInteraction,
    Work,
    WorkConcept,
)
from app.services import library_service
from tests.evaluation.builder import (
    MissingCorpusWorkError,
    build_evaluation_library,
    build_user,
    resolve_works,
    teardown_evaluation_library,
)
from tests.evaluation.dataset import (
    EVALUATION_EMAIL_DOMAIN,
    EVALUATION_USERS,
    PSYCHOLOGICAL,
    SCIENCE_FICTION,
    user_by_case,
)


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    """The whole dataset, keyed by case letter."""
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


async def interactions_for(session: AsyncSession, user_id):
    return await library_service.list_library(
        session, user_id=user_id, include_removed=True, limit=100
    )


# --- the dataset is well formed -------------------------------------------


def test_every_documented_case_is_fully_specified() -> None:
    cases = [user.case for user in EVALUATION_USERS]

    assert len(cases) == len(set(cases))
    for user in EVALUATION_USERS:
        assert user.email.endswith(EVALUATION_EMAIL_DOMAIN)
        assert user.interactions, f"case {user.case} has no interactions"
        for field in (user.exposure, user.rating_pattern, user.reconsumption,
                      user.abandonment, user.expectation):
            assert field and field.strip(), f"case {user.case} is undocumented"


def test_expectations_stay_at_the_level_of_evidence() -> None:
    """Documented readings must not reach for personality labels.

    Turning evidence into traits belongs to a much later phase, and a fixture
    that pre-judges it would bias whatever is built against it.
    """
    forbidden = (
        "open-minded",
        "personality",
        "neurotic",
        "extrovert",
        "introvert",
        "conscientious",
        "agreeable",
    )
    for user in EVALUATION_USERS:
        lowered = user.expectation.lower()
        for word in forbidden:
            assert word not in lowered, f"case {user.case} claims a personality trait"


async def test_the_dataset_builds_deterministically(db_session: AsyncSession) -> None:
    """Same corpus, same dataset -- the ids differ, the shape does not."""
    try:
        first = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")

    shape = []
    for entry in first:
        rows = await interactions_for(db_session, entry.user_id)
        shape.append(
            (
                entry.spec.case,
                sorted((str(r.work_id), r.status, r.rating, r.times_completed) for r in rows),
            )
        )

    await teardown_evaluation_library(db_session)
    second = await build_evaluation_library(db_session)

    rebuilt = []
    for entry in second:
        rows = await interactions_for(db_session, entry.user_id)
        rebuilt.append(
            (
                entry.spec.case,
                sorted((str(r.work_id), r.status, r.rating, r.times_completed) for r in rows),
            )
        )

    assert shape == rebuilt


async def test_a_missing_corpus_work_fails_loudly(db_session: AsyncSession) -> None:
    """A fixture that quietly degrades would invalidate every expectation."""
    from tests.evaluation import builder

    async def pretend_missing(_session):
        raise MissingCorpusWorkError("anilist:0")

    original = builder.resolve_works
    builder.resolve_works = pretend_missing
    try:
        with pytest.raises(MissingCorpusWorkError):
            await build_evaluation_library(db_session)
    finally:
        builder.resolve_works = original


# --- the behavioural cases ------------------------------------------------


async def test_case_a_is_high_ratings_on_psychological_works(
    db_session: AsyncSession, evaluation
) -> None:
    rows = await interactions_for(db_session, evaluation["A"].user_id)

    assert len(rows) == len(PSYCHOLOGICAL)
    assert sorted(r.rating for r in rows) == [8, 9, 9, 9, 10]
    assert all(r.status == "completed" for r in rows)


async def test_case_b_has_identical_exposure_to_case_a_but_opposite_ratings(
    db_session: AsyncSession, evaluation
) -> None:
    """The load-bearing fixture: consumption is not preference."""
    a_rows = await interactions_for(db_session, evaluation["A"].user_id)
    b_rows = await interactions_for(db_session, evaluation["B"].user_id)

    # Exactly the same works, completed in both cases.
    assert {r.work_id for r in a_rows} == {r.work_id for r in b_rows}
    assert {r.status for r in a_rows} == {r.status for r in b_rows} == {"completed"}

    # And ratings that could not disagree more.
    assert min(r.rating for r in a_rows) > max(r.rating for r in b_rows)
    assert sorted(r.rating for r in b_rows) == [3, 4, 4, 5, 5]


async def test_the_two_cases_share_a_concept_so_the_contrast_is_concept_level(
    db_session: AsyncSession, evaluation
) -> None:
    """Both users' works really do carry psychological-depth."""
    rows = await interactions_for(db_session, evaluation["A"].user_id)
    concept = (
        await db_session.execute(select(Concept).where(Concept.slug == "psychological-depth"))
    ).scalar_one()

    tagged = (
        (
            await db_session.execute(
                select(WorkConcept.work_id).where(
                    WorkConcept.concept_id == concept.id,
                    WorkConcept.work_id.in_([r.work_id for r in rows]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(tagged) == {r.work_id for r in rows}


async def test_case_c_is_exposure_with_no_rating_at_all(
    db_session: AsyncSession, evaluation
) -> None:
    rows = await interactions_for(db_session, evaluation["C"].user_id)

    assert rows
    assert all(r.rating is None for r in rows)
    assert all(r.rated_at is None for r in rows)
    assert all(r.status == "completed" for r in rows)
    assert all(r.completed_at is not None for r in rows)


async def test_case_d_abandonment_carries_no_rating_and_no_completion(
    db_session: AsyncSession, evaluation
) -> None:
    """Abandonment must stay ambiguous: no score is attached to it."""
    rows = {r.status: r for r in await interactions_for(db_session, evaluation["D"].user_id)}

    abandoned = rows["abandoned"]
    assert abandoned.rating is None
    assert abandoned.abandoned_at is not None
    assert abandoned.completed_at is None
    assert abandoned.started_at is not None  # it *was* started

    # on_hold is a separate state and must not read as abandonment.
    on_hold = rows["on_hold"]
    assert on_hold.abandoned_at is None
    assert on_hold.rating is None


async def test_case_e_records_reconsumption_without_losing_history(
    db_session: AsyncSession, evaluation
) -> None:
    rows = await interactions_for(db_session, evaluation["E"].user_id)
    repeated = max(rows, key=lambda r: r.times_completed)
    once = min(rows, key=lambda r: r.times_completed)

    assert repeated.times_completed == 3
    assert repeated.times_started == 3
    assert once.times_completed == 1
    # Identical ratings, so only the behaviour separates them.
    assert repeated.rating == once.rating == 9

    # Every cycle is still in the event trail.
    events = await library_service.list_events(
        db_session, user_id=evaluation["E"].user_id, work_id=repeated.work_id
    )
    # Filtered on the event *type* as well: a rating_changed event also
    # carries the status it was set under, which is "completed" here.
    completions = [
        event
        for event in events
        if event.event_type == "status_changed" and event.status_after == "completed"
    ]
    assert len(completions) == 3


async def test_case_f_ratings_are_genuinely_mixed(
    db_session: AsyncSession, evaluation
) -> None:
    rows = await interactions_for(db_session, evaluation["F"].user_id)
    ratings = sorted(r.rating for r in rows)

    assert ratings == [3, 5, 7, 9]
    assert max(ratings) - min(ratings) >= 5  # a wide spread, not a trend


async def test_case_g_spreads_one_concept_across_all_three_domains(
    db_session: AsyncSession, evaluation
) -> None:
    """A preference that cannot be explained by a preference for one medium."""
    rows = await interactions_for(db_session, evaluation["G"].user_id)

    assert len(rows) == len(SCIENCE_FICTION)
    assert all(r.rating >= 9 for r in rows)

    domains = set()
    for row in rows:
        work = await db_session.get(Work, row.work_id)
        await db_session.refresh(work, ["domain"])
        domains.add(work.domain.slug)
    assert domains == {"anime", "literature", "manhwa"}


async def test_case_h_is_a_harsh_rater_whose_top_is_another_users_middle(
    db_session: AsyncSession, evaluation
) -> None:
    """Sets up the normalization problem without solving it."""
    h_rows = await interactions_for(db_session, evaluation["H"].user_id)
    a_rows = await interactions_for(db_session, evaluation["A"].user_id)

    h_ratings = [r.rating for r in h_rows]
    a_ratings = [r.rating for r in a_rows]

    assert max(h_ratings) == 7
    assert min(a_ratings) == 8
    # Every rating this user gave is below every rating case A gave, yet the
    # 7s are this user's own best. Raw comparison across users is meaningless.
    assert max(h_ratings) < min(a_ratings)

    sci_fi_ids = {
        row.work_id
        for row in h_rows
        if row.rating is not None and row.rating >= 6
    }
    assert len(sci_fi_ids) == len(SCIENCE_FICTION)


async def test_case_i_keeps_its_ratings_after_removal(
    db_session: AsyncSession, evaluation
) -> None:
    """Tidying a shelf is not a retraction."""
    user_id = evaluation["I"].user_id

    assert await library_service.list_library(db_session, user_id=user_id) == []

    kept = await interactions_for(db_session, user_id)
    assert len(kept) == 2
    assert sorted(r.rating for r in kept) == [9, 10]
    assert all(r.removed_at is not None for r in kept)
    assert all(r.times_completed == 1 for r in kept)


# --- isolation -------------------------------------------------------------


async def test_the_dataset_creates_no_canonical_content(
    db_session: AsyncSession, evaluation
) -> None:
    """It references the corpus; it never adds to it."""
    works = await db_session.execute(select(func.count()).select_from(Work))
    resolved = await resolve_works(db_session)

    assert works.scalar_one() >= len(resolved)
    # Every referenced work already existed; none was created by the builder.
    for work_id in resolved.values():
        assert await db_session.get(Work, work_id) is not None


EVALUATION_LIKE = f"%{EVALUATION_EMAIL_DOMAIN}"


async def _dataset_counts(
    session: AsyncSession, *, inside: bool
) -> dict[str, int]:
    """Users, interactions and events, inside or outside the dataset.

    `inside=False` is everything the dataset did not create -- which on a
    development database means real accounts. Teardown must leave those
    exactly as it found them, and that is what the whole-table counts these
    tests used to make could never say.
    """
    users = (
        select(User.id)
        .where(
            User.email.like(EVALUATION_LIKE)
            if inside
            else ~User.email.like(EVALUATION_LIKE)
        )
        .scalar_subquery()
    )
    interactions = (
        select(UserContentInteraction.id)
        .where(UserContentInteraction.user_id.in_(users))
        .scalar_subquery()
    )

    async def count(model, where) -> int:
        return (
            await session.execute(select(func.count()).select_from(model).where(where))
        ).scalar_one()

    return {
        "users": await count(User, User.id.in_(users)),
        "interactions": await count(
            UserContentInteraction, UserContentInteraction.id.in_(interactions)
        ),
        "events": await count(
            UserContentEvent, UserContentEvent.interaction_id.in_(interactions)
        ),
    }


async def test_evaluation_users_are_confined_to_the_reserved_domain(
    db_session: AsyncSession, evaluation
) -> None:
    """The builder creates exactly the documented users, and no other address.

    Scoped to the reserved domain: the point is that the builder reaches for
    nothing outside it, not that the database contains nothing else. A
    development database with a real account in it is an ordinary situation.
    """
    built = sorted(entry.spec.email for entry in evaluation.values())
    assert built
    assert all(email.endswith(EVALUATION_EMAIL_DOMAIN) for email in built)

    stored = (
        (
            await db_session.execute(
                select(User.email).where(User.email.like(EVALUATION_LIKE))
            )
        )
        .scalars()
        .all()
    )
    # Exactly the documented cases: no duplicate, no stray, no omission.
    assert sorted(stored) == built


async def test_teardown_removes_everything_it_created(
    db_session: AsyncSession, evaluation
) -> None:
    """Everything the dataset made goes, and nothing else is touched."""
    before_works = (
        await db_session.execute(select(func.count()).select_from(Work))
    ).scalar_one()
    outsiders_before = await _dataset_counts(db_session, inside=False)
    assert (await _dataset_counts(db_session, inside=True))["users"] == len(
        EVALUATION_USERS
    )

    removed = await teardown_evaluation_library(db_session)

    assert removed == len(EVALUATION_USERS)
    # The dataset is gone, down to its events.
    assert await _dataset_counts(db_session, inside=True) == {
        "users": 0,
        "interactions": 0,
        "events": 0,
    }
    # And teardown reached only what the dataset made: whatever else the
    # database held is still there, with its library and history intact.
    assert await _dataset_counts(db_session, inside=False) == outsiders_before

    after_works = (
        await db_session.execute(select(func.count()).select_from(Work))
    ).scalar_one()
    assert after_works == before_works


async def test_one_evaluation_user_cannot_see_another(
    db_session: AsyncSession, evaluation
) -> None:
    a_rows = await interactions_for(db_session, evaluation["A"].user_id)
    b_rows = await interactions_for(db_session, evaluation["B"].user_id)

    assert {r.id for r in a_rows}.isdisjoint({r.id for r in b_rows})
    # The same canonical works, held separately.
    assert {r.work_id for r in a_rows} == {r.work_id for r in b_rows}


async def test_a_single_case_can_be_built_on_its_own(
    db_session: AsyncSession,
) -> None:
    """Useful for a focused test that does not need the whole dataset."""
    try:
        works = await resolve_works(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")

    built = await build_user(db_session, user_by_case("D"), works)
    rows = await interactions_for(db_session, built.user_id)

    assert {r.status for r in rows} == {"abandoned", "on_hold"}
