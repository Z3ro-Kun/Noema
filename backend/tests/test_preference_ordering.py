"""Phase 1R: the order a reader meets their preference signals in.

Two kinds of test. The first are unit tests over synthetic `ConceptEvidence`,
because an ordering is a pure function and deserves to be pinned exactly. The
second run both orderings against the Phase 1N evaluation library and assert
the load-bearing property of the whole phase: **only the sequence changes.**

Nothing here asserts that one ordering is better. That question is answered
by the comparison artifact and the decision recorded in `docs/architecture.md`;
these tests exist so the answer cannot quietly stop being true.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.evidence import (
    DIRECTION_NEGATIVE,
    DIRECTION_POSITIVE,
    ConceptEvidence,
    build_preference_profile,
)
from app.services.preference.ordering import (
    ORDERING_CONFIDENCE_FIRST,
    ORDERING_EVIDENCE_FIRST,
    BAND_RANK,
    confidence_band,
    engine_key,
    order_by_confidence,
    order_by_evidence,
    order_signals,
)
from app.services.preference.product import build_preference_overview
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


def concept(
    slug: str,
    *,
    confidence: float,
    evidence: float | None,
    name: str | None = None,
    rated: int = 1,
) -> ConceptEvidence:
    """A bare `ConceptEvidence` carrying only what an ordering may read."""
    return ConceptEvidence(
        concept_slug=slug,
        concept_name=name if name is not None else slug.replace("-", " ").title(),
        concept_type="theme",
        works_rated=rated,
        preference_evidence=evidence,
        direction=(
            DIRECTION_POSITIVE if (evidence or 0.0) >= 0 else DIRECTION_NEGATIVE
        ),
        confidence=confidence,
    )


def slugs(items) -> list[str]:
    return [item.concept_slug for item in items]


# --- the ordering itself ---------------------------------------------------


def test_confidence_leads_the_ordering() -> None:
    ordered = order_by_confidence(
        [
            concept("weak-but-extreme", confidence=0.2, evidence=0.95),
            concept("well-supported", confidence=0.7, evidence=0.4),
            concept("middling", confidence=0.45, evidence=0.6),
        ]
    )

    assert slugs(ordered) == ["well-supported", "middling", "weak-but-extreme"]


def test_a_thin_signal_no_longer_leads_a_better_supported_one() -> None:
    """The baseline's actual weakness, stated as a test.

    Phase 1P's band grouping already stops a *single* rating from leading the
    page -- one rating cannot exceed a confidence of 0.25, which is the low
    band. What it does not stop is the same failure *inside* a band: two
    works rated 10 and 9 produce a larger evidence value than five whose
    ratings run 10 down to 8, and both land in the moderate band, so the
    thinner signal led. This is case A exactly.
    """
    thin = concept("two-works", confidence=0.40, evidence=0.92, rated=2)
    supported = concept("five-works", confidence=0.58, evidence=0.79, rated=5)

    assert confidence_band(thin.confidence) == confidence_band(supported.confidence)
    assert slugs(order_by_evidence([supported, thin]))[0] == "two-works"
    assert slugs(order_by_confidence([thin, supported]))[0] == "five-works"


def test_ties_fall_to_evidence_magnitude_then_name() -> None:
    ordered = order_by_confidence(
        [
            concept("c", confidence=0.5, evidence=0.2, name="Cee"),
            concept("a", confidence=0.5, evidence=0.2, name="Aye"),
            concept("b", confidence=0.5, evidence=0.9, name="Bee"),
        ]
    )

    assert slugs(ordered) == ["b", "a", "c"]


def test_a_well_evidenced_dislike_is_not_buried_under_a_weaker_liking() -> None:
    """Why the tie-break is |evidence| and not the signed value.

    Signed evidence would sort every negative below every positive at equal
    confidence -- a claim about which direction matters, smuggled in through
    a tie-breaker.
    """
    ordered = order_by_confidence(
        [
            concept("mild-liking", confidence=0.5, evidence=0.2),
            concept("strong-dislike", confidence=0.5, evidence=-0.8),
        ]
    )

    assert slugs(ordered) == ["strong-dislike", "mild-liking"]
    assert ordered[0].direction == DIRECTION_NEGATIVE


def test_direction_is_never_touched_by_ordering() -> None:
    before = [
        concept("up", confidence=0.6, evidence=0.5),
        concept("down", confidence=0.9, evidence=-0.5),
    ]
    directions = {item.concept_slug: item.direction for item in before}

    for ordered in (order_by_confidence(before), order_by_evidence(before)):
        assert {item.concept_slug: item.direction for item in ordered} == directions


def test_confidence_ordering_subsumes_the_confidence_bands() -> None:
    """A band is monotone in confidence, so grouping is a consequence."""
    items = [
        concept("low", confidence=0.1, evidence=1.0),
        concept("high", confidence=0.8, evidence=0.1),
        concept("moderate", confidence=0.4, evidence=0.5),
    ]

    bands = [
        BAND_RANK[confidence_band(item.confidence)] for item in order_by_confidence(items)
    ]
    assert bands == sorted(bands)


def test_missing_evidence_is_treated_as_zero_not_as_an_error() -> None:
    ordered = order_by_confidence(
        [
            concept("unrated", confidence=0.0, evidence=None),
            concept("rated", confidence=0.5, evidence=-0.3),
        ]
    )

    assert slugs(ordered) == ["rated", "unrated"]


def test_the_baseline_ordering_is_phase_1p_exactly() -> None:
    """Band grouping over the engine's own order, stably."""
    items = [
        concept("low-but-extreme", confidence=0.1, evidence=1.0),
        concept("high-a", confidence=0.8, evidence=0.2, name="High A"),
        concept("high-b", confidence=0.7, evidence=0.5, name="High B"),
        concept("moderate", confidence=0.4, evidence=0.9),
    ]

    engine_ordered = sorted(items, key=engine_key)
    expected = sorted(
        engine_ordered, key=lambda item: BAND_RANK[confidence_band(item.confidence)]
    )

    assert slugs(order_by_evidence(items)) == slugs(expected)
    # And it really is evidence-first inside the band.
    assert slugs(order_by_evidence(items))[:2] == ["high-b", "high-a"]


def test_an_unknown_ordering_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(ValueError, match="unknown ordering"):
        order_signals([], "by-vibes")


def test_ordering_reads_nothing_that_could_carry_frequency() -> None:
    """No frequency leakage, stated where it cannot rot.

    `ConceptEvidence` has no document-frequency field at all, so the ordering
    has nothing to leak; this pins that the module never grows an import that
    would give it one.
    """
    import ast
    import pathlib

    source = pathlib.Path("app/services/preference/ordering.py").read_text(
        encoding="utf-8"
    )
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any(
        name.endswith(("frequency", "experiment")) for name in imported
    ), imported
    assert not hasattr(ConceptEvidence, "document_frequency")


# --- against the evaluation library: only the sequence moves ---------------


def comparable(signal) -> dict:
    """Everything about a signal except where it sits in the list."""
    return {
        "concept_name": signal.concept_name,
        "concept_type": signal.concept_type,
        "direction": signal.direction,
        "confidence_band": signal.confidence_band,
        "evidence": signal.evidence.model_dump(),
        "contributions": [work.model_dump() for work in signal.contributions],
    }


@pytest.mark.parametrize("case", ["A", "B", "E", "F", "G", "H", "I"])
async def test_only_the_sequence_changes(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """The phase's central claim, per case.

    Same concepts, same directions, same bands, same counts, same
    contributing works. Only the order.
    """
    user_id = evaluation[case].user_id
    baseline = await build_preference_overview(
        db_session, user_id, ordering=ORDERING_EVIDENCE_FIRST
    )
    candidate = await build_preference_overview(
        db_session, user_id, ordering=ORDERING_CONFIDENCE_FIRST
    )

    assert {item.concept_slug for item in baseline.signals} == {
        item.concept_slug for item in candidate.signals
    }
    assert {item.concept_slug: comparable(item) for item in baseline.signals} == {
        item.concept_slug: comparable(item) for item in candidate.signals
    }
    assert baseline.summary == candidate.summary


@pytest.mark.parametrize("case", ["A", "C", "D", "G"])
async def test_unrated_exposure_is_untouched_by_signal_ordering(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """The separate section stays separate, and stays in its own order."""
    user_id = evaluation[case].user_id
    baseline = await build_preference_overview(
        db_session, user_id, ordering=ORDERING_EVIDENCE_FIRST
    )
    candidate = await build_preference_overview(
        db_session, user_id, ordering=ORDERING_CONFIDENCE_FIRST
    )

    assert [item.concept_slug for item in baseline.awaiting_ratings] == [
        item.concept_slug for item in candidate.awaiting_ratings
    ]
    for signal in candidate.awaiting_ratings:
        assert not hasattr(signal, "direction")
        assert signal.concept_slug not in {
            item.concept_slug for item in candidate.signals
        }


async def test_the_engine_values_are_not_mutated_by_ordering(
    db_session: AsyncSession, evaluation
) -> None:
    """An ordering that sorted in place, or rounded, would show up here."""
    user_id = evaluation["A"].user_id
    profile = await build_preference_profile(db_session, user_id)
    before = {
        item.concept_slug: (item.preference_evidence, item.confidence, item.direction)
        for item in profile.concepts
    }

    order_by_confidence(profile.concepts)
    order_by_evidence(profile.concepts)

    after = {
        item.concept_slug: (item.preference_evidence, item.confidence, item.direction)
        for item in profile.concepts
    }
    assert before == after


@pytest.mark.parametrize(
    ("case", "target", "expected"),
    [
        ("A", "psychological-depth", 1),
        ("B", "psychological-depth", 1),
        ("E", "crime-and-investigation", 2),
        ("G", "science-fiction", 1),
        ("H", "science-fiction", 2),
        ("I", "science-fiction", 2),
    ],
)
async def test_documented_concepts_reach_the_measured_positions(
    db_session: AsyncSession, evaluation, case: str, target: str, expected: int
) -> None:
    """The comparison, pinned.

    E stays second because its two works carry `adventure` and
    `crime-and-investigation` on the identical pair of 9s: confidence,
    evidence and rated count are equal, so the tie resolves on name and no
    ordering could separate them without inventing a signal.
    """
    overview = await build_preference_overview(
        db_session, evaluation[case].user_id, ordering=ORDERING_CONFIDENCE_FIRST
    )

    ranks = {item.concept_slug: index for index, item in enumerate(overview.signals, 1)}
    assert ranks[target] == expected


async def test_confidence_ordering_still_groups_the_bands(
    db_session: AsyncSession, evaluation
) -> None:
    """The Phase 1P guarantee survives as a consequence, not a second pass."""
    for case in ("A", "F", "H", "I"):
        overview = await build_preference_overview(
            db_session, evaluation[case].user_id, ordering=ORDERING_CONFIDENCE_FIRST
        )
        bands = [BAND_RANK[item.confidence_band] for item in overview.signals]
        assert bands == sorted(bands), case


async def test_cases_without_ratings_have_no_signals_under_either_ordering(
    db_session: AsyncSession, evaluation
) -> None:
    """Ordering cannot manufacture a direction out of an empty list."""
    for case in ("C", "D"):
        for ordering in (ORDERING_EVIDENCE_FIRST, ORDERING_CONFIDENCE_FIRST):
            overview = await build_preference_overview(
                db_session, evaluation[case].user_id, ordering=ordering
            )
            assert overview.signals == [], (case, ordering)
            assert overview.awaiting_ratings


async def test_the_cross_domain_signal_leads_its_case(
    db_session: AsyncSession, evaluation
) -> None:
    """Case G: the concept spanning three domains, not a two-anime coincidence."""
    overview = await build_preference_overview(
        db_session, evaluation["G"].user_id, ordering=ORDERING_CONFIDENCE_FIRST
    )

    leading = overview.signals[0]
    assert leading.concept_slug == "science-fiction"
    assert leading.direction == "positive"
    assert {work.domain_name for work in leading.contributions} == {
        "Anime",
        "Literature",
        "Manga & Manhwa",
    }


async def test_removed_works_still_carry_their_signal(
    db_session: AsyncSession, evaluation
) -> None:
    """Case I: ordering must not quietly drop evidence from a tidied shelf."""
    overview = await build_preference_overview(
        db_session, evaluation["I"].user_id, ordering=ORDERING_CONFIDENCE_FIRST
    )

    signal = next(
        item for item in overview.signals if item.concept_slug == "science-fiction"
    )
    assert signal.contributions
    assert all(work.in_library is False for work in signal.contributions)


async def test_the_ordering_choice_never_reaches_the_product_contract(
    db_session: AsyncSession, evaluation
) -> None:
    """No raw confidence, no rank, no ordering name in the payload."""
    overview = await build_preference_overview(
        db_session, evaluation["A"].user_id, ordering=ORDERING_CONFIDENCE_FIRST
    )
    payload = overview.model_dump_json()

    for forbidden in (
        "ordering",
        "rank",
        "salience",
        "specificity",
        "document_frequency",
        '"confidence":',
        "preference_evidence",
        "normalized_rating",
    ):
        assert forbidden not in payload, forbidden
