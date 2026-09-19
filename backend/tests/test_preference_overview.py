"""The product-facing preference overview.

Phase 1O's evidence is an inspection surface; this is what a reader sees. The
tests are mostly about what the projection *refuses* to pass through, because
that is where the phase's boundary actually lives.

Uses the Phase 1N evaluation library, so the shapes asserted here are the ones
a real profile produces.
"""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters
from app.services.preference.product import build_preference_overview, confidence_band
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


def by_slug(overview):
    return {item.concept_slug: item for item in overview.signals}


# --- banding ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.0, "low"),
        (0.25, "low"),
        (0.34, "low"),
        (0.35, "moderate"),
        (0.59, "moderate"),
        (0.6, "high"),
        (1.0, "high"),
    ],
)
def test_confidence_is_banded_at_the_documented_thresholds(
    confidence: float, expected: str
) -> None:
    assert confidence_band(confidence) == expected


def test_banding_follows_its_parameters() -> None:
    lenient = PreferenceParameters(confidence_moderate_from=0.1, confidence_high_from=0.2)

    assert confidence_band(0.15, lenient) == "moderate"
    assert confidence_band(0.15) == "low"


# --- what the projection refuses to pass through ---------------------------


async def test_the_overview_carries_no_engine_internals(
    db_session: AsyncSession, evaluation
) -> None:
    overview = await build_preference_overview(db_session, evaluation["A"].user_id)
    serialised = json.dumps(overview.model_dump(mode="json"))

    for forbidden in (
        "baseline",
        "spread",
        "normalized_rating",
        "normalization",
        "preference_evidence",
        "rating_signal",
        "exposure",
        "engagement",
        "concept_confidence",
        "supporting_labels",
        "user_id",
        "domain_slug",
    ):
        assert forbidden not in serialised, f"{forbidden} reached the product surface"


async def test_no_raw_confidence_number_is_exposed(
    db_session: AsyncSession, evaluation
) -> None:
    """A band, not a float: 0.54 on screen invites being read as 54% certain."""
    overview = await build_preference_overview(db_session, evaluation["A"].user_id)

    for signal in overview.signals:
        assert signal.confidence_band in {"low", "moderate", "high"}
        assert not hasattr(signal, "confidence")


# --- the documented cases, as the product renders them ---------------------


async def test_case_a_and_b_render_opposite_directions(
    db_session: AsyncSession, evaluation
) -> None:
    a = by_slug(await build_preference_overview(db_session, evaluation["A"].user_id))
    b = by_slug(await build_preference_overview(db_session, evaluation["B"].user_id))

    assert a["psychological-depth"].direction == "positive"
    assert b["psychological-depth"].direction == "negative"
    # Same consumption underneath.
    assert (
        a["psychological-depth"].evidence.works_completed
        == b["psychological-depth"].evidence.works_completed
    )


async def test_case_c_produces_no_signals_only_awaiting_ratings(
    db_session: AsyncSession, evaluation
) -> None:
    """Unrated completions must never reach the signals list."""
    overview = await build_preference_overview(db_session, evaluation["C"].user_id)

    assert overview.signals == []
    assert overview.awaiting_ratings
    assert overview.summary.works_rated == 0
    for item in overview.awaiting_ratings:
        assert item.evidence.works_rated == 0
        assert item.evidence.works_completed > 0


async def test_case_d_reports_abandonment_and_on_hold_separately(
    db_session: AsyncSession, evaluation
) -> None:
    overview = await build_preference_overview(db_session, evaluation["D"].user_id)

    assert overview.signals == []  # nothing rated, so no direction anywhere
    abandoned = [item for item in overview.awaiting_ratings if item.evidence.works_abandoned]
    on_hold = [item for item in overview.awaiting_ratings if item.evidence.works_on_hold]

    assert abandoned and on_hold
    # Both are behaviour, and neither became a rating.
    for item in abandoned + on_hold:
        assert item.evidence.works_rated == 0


async def test_case_e_surfaces_reconsumption_without_touching_the_rating(
    db_session: AsyncSession, evaluation
) -> None:
    overview = await build_preference_overview(db_session, evaluation["E"].user_id)
    signal = by_slug(overview)["crime-and-investigation"]

    assert signal.evidence.works_reconsumed == 1
    assert signal.evidence.total_completions > signal.evidence.works_completed
    # The rating average is the plain one, unamplified.
    assert signal.evidence.rating_mean == 9.0


async def test_case_g_contributions_span_three_domains(
    db_session: AsyncSession, evaluation
) -> None:
    signal = by_slug(await build_preference_overview(db_session, evaluation["G"].user_id))[
        "science-fiction"
    ]

    assert {item.domain_name for item in signal.contributions} == {
        "Anime",
        "Literature",
        "Manga & Manhwa",
    }


async def test_case_i_keeps_removed_works_visible_as_evidence(
    db_session: AsyncSession, evaluation
) -> None:
    signal = by_slug(await build_preference_overview(db_session, evaluation["I"].user_id))[
        "science-fiction"
    ]

    assert signal.contributions
    assert all(item.in_library is False for item in signal.contributions)
    assert signal.direction == "positive"


# --- ordering and presentation --------------------------------------------


async def test_better_supported_signals_are_listed_first(
    db_session: AsyncSession, evaluation
) -> None:
    """A concept resting on one rating must not lead the page."""
    overview = await build_preference_overview(db_session, evaluation["A"].user_id)
    order = {"high": 0, "moderate": 1, "low": 2}
    bands = [order[item.confidence_band] for item in overview.signals]

    assert bands == sorted(bands)


async def test_contributions_put_rated_works_first_and_highest_first(
    db_session: AsyncSession, evaluation
) -> None:
    signal = by_slug(await build_preference_overview(db_session, evaluation["A"].user_id))[
        "psychological-depth"
    ]
    ratings = [item.rating for item in signal.contributions]

    rated = [value for value in ratings if value is not None]
    assert ratings[: len(rated)] == rated
    assert rated == sorted(rated, reverse=True)


async def test_domain_names_are_display_names_not_slugs(
    db_session: AsyncSession, evaluation
) -> None:
    overview = await build_preference_overview(db_session, evaluation["A"].user_id)
    names = {item.domain_name for signal in overview.signals for item in signal.contributions}

    assert "manhwa" not in names
    assert names <= {"Anime", "Literature", "Manga & Manhwa"}


async def test_the_summary_reports_context_as_a_flag_not_a_number(
    db_session: AsyncSession, evaluation
) -> None:
    overview = await build_preference_overview(db_session, evaluation["A"].user_id)

    assert overview.summary.rating_context_established is True
    assert overview.summary.works_rated == 5
    assert overview.summary.signals_with_direction == len(overview.signals)


async def test_an_empty_history_produces_an_empty_overview(db_session: AsyncSession) -> None:
    from app.services import auth_service

    user = await auth_service.register_user(
        db_session, email="empty@overview.test", password="a-sufficiently-long-password"
    )

    overview = await build_preference_overview(db_session, user.id)

    assert overview.signals == []
    assert overview.awaiting_ratings == []
    assert overview.summary.total_interactions == 0
    assert overview.summary.rating_context_established is False


async def test_the_projection_changes_no_engine_value(
    db_session: AsyncSession, evaluation
) -> None:
    """The overview is a view: directions must match the engine exactly."""
    from app.services.preference.evidence import build_preference_profile

    profile = await build_preference_profile(db_session, evaluation["H"].user_id)
    overview = await build_preference_overview(db_session, evaluation["H"].user_id)

    engine = {item.concept_slug: item for item in profile.concepts}
    for signal in overview.signals:
        assert signal.direction == engine[signal.concept_slug].direction
        assert signal.confidence_band == confidence_band(
            engine[signal.concept_slug].confidence, DEFAULT_PARAMETERS
        )
        assert signal.evidence.works_rated == engine[signal.concept_slug].works_rated
