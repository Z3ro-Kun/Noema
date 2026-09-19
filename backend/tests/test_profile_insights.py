"""Phase 1U: structured observations over a composed profile.

The evaluation cases this phase was asked for are X through AJ. Most of them
name a *shape* the library already has -- a single strong pattern, a
combination, cross-domain support, redundant patterns -- and cloning an
existing fixture to give it a second letter would add a user without adding a
measurement. So each of X-AJ is a named test here, and the mapping is written
into its docstring; only AG and AH, whose shapes did not exist, were added to
the dataset.

    X   single strong pattern         -> case L  (one established pattern)
    Y   multiple independent patterns -> case P  (nine distinct findings)
    Z   combination selected          -> case S  (the one established pair)
    AA  cross-domain                  -> case V  (three domains)
    AB  positive and negative         -> case T
    AC  emerging beside established   -> case U
    AD  reconsumption                 -> case E  (one work completed 3x)
    AE  abandonment without rating    -> case D
    AF  no ratings at all             -> case C
    AG  fictional-theme safety        -> case AG (new)
    AH  unknown rating cause          -> case AH (new)
    AI  redundant patterns            -> case W  (ten patterns, one finding)
    AJ  no fabrication                -> case Q  (fewer than the cap)
"""

import uuid
from dataclasses import fields

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.insights import (
    MAX_EMERGING_INSIGHTS,
    MAX_PATTERN_INSIGHTS,
    OBSERVATION_COMBINATION,
    OBSERVATION_EMERGING,
    OBSERVATION_OPPOSING,
    OBSERVATION_PATTERN,
    OBSERVATIONS,
    PRESENTATION_NEGATIVE_FEATURE,
    PRESENTATION_EMERGING_COMBINATION,
    PRESENTATION_EMERGING_FEATURE,
    PRESENTATION_ENJOYS_COMBINATION,
    PRESENTATION_ENJOYS_FEATURE,
    PRESENTATION_KEYS,
    PRESENTATION_MIXED_DIRECTIONS,
    InsightEvidence,
    InsightWork,
    ProfileInsights,
    TasteInsight,
    build_profile_insights,
    derive_insights,
)
from app.services.preference.profile import (
    ComposedProfile,
    SelectedPattern,
    compose_profile,
)
from app.services.preference.taste import (
    KIND_COMBINATION,
    KIND_INDIVIDUAL,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    Feature,
    SupportingWork,
    TastePattern,
)
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


async def insights_for(session, evaluation, case: str) -> ProfileInsights:
    return await build_profile_insights(session, evaluation[case].user_id)


# --- constructed profiles, for rules the corpus cannot reach ---------------


def work(rating: int | None = 9, domain: str = "anime") -> SupportingWork:
    return SupportingWork(
        work_id=uuid.uuid4(),
        title=f"work-{uuid.uuid4().hex[:6]}",
        domain_slug=domain,
        status="completed",
        rating=rating,
        normalized_rating=None if rating is None else 0.8,
        times_completed=1,
        in_library=True,
    )


def pattern(
    keys: tuple[str, ...],
    works: tuple[SupportingWork, ...],
    *,
    status: str = STATUS_ESTABLISHED,
    confidence: float = 0.5,
    evidence: float = 0.8,
    direction: str = "positive",
) -> TastePattern:
    features = tuple(
        Feature(key=key, name=key.replace("-", " ").title(), family="theme")
        for key in keys
    )
    return TastePattern(
        features=features,
        kind=KIND_INDIVIDUAL if len(features) == 1 else KIND_COMBINATION,
        status=status,
        direction=direction,
        preference_evidence=evidence,
        confidence=confidence,
        works_rated=sum(1 for w in works if w.rating is not None),
        supporting_works=works,
        domains=tuple(sorted({w.domain_slug for w in works})),
    )


def composed(key: list[TastePattern], early: list[TastePattern] | None = None):
    return ComposedProfile(
        user_id=uuid.uuid4(),
        key_patterns=[SelectedPattern(pattern=p) for p in key],
        early_signals=[SelectedPattern(pattern=p) for p in (early or [])],
    )


# --- the closed vocabularies -----------------------------------------------


async def test_every_emitted_value_comes_from_a_closed_set(
    db_session: AsyncSession, evaluation
) -> None:
    """The structural form of "this layer does not write sentences".

    A fixed enumeration cannot grow into prose, so nothing downstream can
    receive wording this module invented.
    """
    seen = set()
    for case in ("A", "B", "G", "S", "T", "V", "W", "AG"):
        result = await insights_for(db_session, evaluation, case)
        for insight in result.insights:
            assert insight.observation in OBSERVATIONS
            assert insight.presentation_key in PRESENTATION_KEYS
            seen.add((insight.observation, insight.presentation_key))

    # And the fixtures really do exercise more than one of them.
    assert len(seen) >= 4


def test_a_negative_pattern_is_not_described_as_avoidance() -> None:
    """`negative_feature`, not `avoids_feature`.

    The reader finished these works and rated them poorly. A key implying
    avoidance would describe behaviour the evidence contradicts.
    """
    disliked = pattern(("grim",), (work(2), work(3), work(2)), direction="negative")

    result = derive_insights(composed([disliked]))

    assert result.insights[0].presentation_key == PRESENTATION_NEGATIVE_FEATURE
    assert "avoid" not in " ".join(PRESENTATION_KEYS)


def test_an_emerging_pattern_cannot_be_phrased_as_a_settled_preference() -> None:
    unsettled = pattern(
        ("maybe",), (work(), work()), status=STATUS_EMERGING, confidence=0.4
    )

    result = derive_insights(composed([], [unsettled]))

    insight = result.insights[0]
    assert insight.observation == OBSERVATION_EMERGING
    assert insight.presentation_key == PRESENTATION_EMERGING_FEATURE
    # The direction is still recorded -- it is just not in the key.
    assert insight.evidence.direction == "positive"


# --- caps, and never padding ------------------------------------------------


def test_the_caps_are_ceilings() -> None:
    key = [
        pattern((f"f{index:02d}",), (work(), work(), work()), confidence=0.9 - index / 100)
        for index in range(8)
    ]
    early = [
        pattern((f"e{index}",), (work(), work()), status=STATUS_EMERGING)
        for index in range(6)
    ]

    result = derive_insights(composed(key, early))

    assert result.diagnostics.pattern_insights == MAX_PATTERN_INSIGHTS
    assert result.diagnostics.emerging_insights == MAX_EMERGING_INSIGHTS
    assert result.diagnostics.pattern_insights_capped == 3
    assert result.diagnostics.emerging_insights_capped == 3


def test_nothing_is_added_to_reach_the_cap() -> None:
    result = derive_insights(composed([pattern(("only",), (work(), work(), work()))]))

    assert len(result.insights) == 1
    assert result.diagnostics.pattern_insights_capped == 0


def test_an_empty_profile_produces_no_insights() -> None:
    result = derive_insights(composed([]))

    assert result.insights == []
    assert result.diagnostics.profile_insights == 0


# --- ordering ---------------------------------------------------------------


def test_established_insights_come_before_emerging_ones() -> None:
    settled = pattern(("settled",), (work(), work(), work()), confidence=0.4)
    unsettled = pattern(
        ("unsettled",), (work(), work()), status=STATUS_EMERGING, confidence=0.9
    )

    result = derive_insights(composed([settled], [unsettled]))

    assert result.insights[0].observation == OBSERVATION_PATTERN
    assert result.insights[-1].observation == OBSERVATION_EMERGING


def test_breadth_of_domains_does_not_order_insights() -> None:
    """Section 6: provenance is not a hidden ranking term."""
    broad = pattern(
        ("broad",),
        (work(domain="anime"), work(domain="literature"), work(domain="manhwa")),
        confidence=0.4,
    )
    deep = pattern(
        ("deep",),
        tuple(work(domain="anime") for _ in range(5)),
        confidence=0.7,
    )

    result = derive_insights(composed([broad, deep]))

    assert [i.pattern_keys[0] for i in result.insights] == ["deep", "broad"]
    assert result.insights[1].evidence.domain_count == 3
    # Three domains with one work each is not five works, and the structure
    # keeps the two facts apart.
    assert result.insights[1].evidence.works_rated == 3


def test_derivation_is_deterministic_under_shuffled_input() -> None:
    patterns = [
        pattern((f"f{index}",), (work(), work(), work()), confidence=0.5)
        for index in range(6)
    ]

    forward = derive_insights(composed(list(patterns)))
    backward = derive_insights(composed(list(reversed(patterns))))

    assert [i.pattern_keys for i in forward.insights] == [
        i.pattern_keys for i in backward.insights
    ]
    assert [i.presentation_key for i in forward.insights] == [
        i.presentation_key for i in backward.insights
    ]


# --- semantic invariance ----------------------------------------------------


@pytest.mark.parametrize("case", ["A", "S", "T", "V", "W"])
async def test_deriving_insights_mutates_nothing(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """Section 17: a derived view, and it stays derived."""
    profile = await compose_profile(db_session, evaluation[case].user_id)
    snapshot = {
        item.key: (
            item.pattern.preference_evidence,
            item.pattern.confidence,
            item.pattern.direction,
            item.pattern.status,
            item.pattern.domains,
            tuple(w.work_id for w in item.pattern.supporting_works),
            item.pattern.works_reconsumed,
            item.pattern.total_completions,
            item.pattern.works_abandoned,
            item.pattern.abandonment_signal,
            item.pattern.reconsumption_signal,
            item.pattern.constituent_evidence,
        )
        for item in [*profile.key_patterns, *profile.early_signals]
    }

    derive_insights(profile)

    after = {
        item.key: (
            item.pattern.preference_evidence,
            item.pattern.confidence,
            item.pattern.direction,
            item.pattern.status,
            item.pattern.domains,
            tuple(w.work_id for w in item.pattern.supporting_works),
            item.pattern.works_reconsumed,
            item.pattern.total_completions,
            item.pattern.works_abandoned,
            item.pattern.abandonment_signal,
            item.pattern.reconsumption_signal,
            item.pattern.constituent_evidence,
        )
        for item in [*profile.key_patterns, *profile.early_signals]
    }
    assert snapshot == after


@pytest.mark.parametrize("case", ["A", "G", "S", "V"])
async def test_every_insight_traces_back_to_a_selected_pattern(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """Section 4: nothing may be created from intuition over the works."""
    profile = await compose_profile(db_session, evaluation[case].user_id)
    result = derive_insights(profile)

    known = {item.key for item in [*profile.key_patterns, *profile.early_signals]}
    for insight in result.insights:
        assert insight.pattern_keys
        for key in insight.pattern_keys:
            assert key in known, key


@pytest.mark.parametrize("case", ["A", "S", "V"])
async def test_supporting_works_survive_intact(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    profile = await compose_profile(db_session, evaluation[case].user_id)
    result = derive_insights(profile)
    by_key = {item.key: item for item in profile.key_patterns}

    for insight in result.of_type(OBSERVATION_PATTERN) + result.of_type(
        OBSERVATION_COMBINATION
    ):
        source = by_key[insight.pattern_keys[0]].pattern
        assert [w.work_id for w in insight.evidence.supporting_works] == [
            w.work_id for w in source.supporting_works
        ]
        for emitted, original in zip(
            insight.evidence.supporting_works, source.supporting_works
        ):
            assert emitted.title == original.title
            assert emitted.rating == original.rating
            assert emitted.domain == original.domain_slug
            assert emitted.times_completed == original.times_completed


# --- the evaluation cases, X through AJ -------------------------------------


async def test_case_x_single_strong_pattern(
    db_session: AsyncSession, evaluation
) -> None:
    """X: one clearly established positive pattern -> one insight (case L)."""
    result = await insights_for(db_session, evaluation, "L")

    highlights = result.of_type(OBSERVATION_PATTERN)
    assert len(highlights) == 1
    assert highlights[0].pattern_keys == ("crime-and-investigation",)
    assert highlights[0].presentation_key == PRESENTATION_ENJOYS_FEATURE


async def test_case_y_multiple_independent_patterns(
    db_session: AsyncSession, evaluation
) -> None:
    """Y: several patterns on different works -> several non-redundant insights (case P)."""
    result = await insights_for(db_session, evaluation, "P")

    highlights = result.of_type(OBSERVATION_PATTERN)
    assert len(highlights) == MAX_PATTERN_INSIGHTS
    keys = [i.pattern_keys[0] for i in highlights]
    assert len(set(keys)) == len(keys)
    # Different evidence, not the same works relabelled.
    supports = {
        tuple(w.work_id for w in i.evidence.supporting_works) for i in highlights
    }
    assert len(supports) == len(highlights)


async def test_case_z_combination_keeps_both_features(
    db_session: AsyncSession, evaluation
) -> None:
    """Z: a combination is never flattened into one invented concept (case S)."""
    result = await insights_for(db_session, evaluation, "S")

    combinations = result.of_type(OBSERVATION_COMBINATION)
    assert len(combinations) == 1
    insight = combinations[0]
    assert insight.is_combination
    assert [f.key for f in insight.features] == [
        "crime-and-investigation",
        "urban-modernity",
    ]
    assert insight.presentation_key == PRESENTATION_ENJOYS_COMBINATION
    # Each part's own evidence travels with it, so the pair is visibly not an
    # inference from its constituents.
    assert len(insight.evidence.constituent_evidence) == 2


async def test_case_aa_cross_domain_provenance(
    db_session: AsyncSession, evaluation
) -> None:
    """AA: three domains, preserved as provenance and not as a score (case V)."""
    result = await insights_for(db_session, evaluation, "V")

    science = next(
        i for i in result.insights if i.pattern_keys[0] == "science-fiction"
    )
    assert set(science.evidence.domains) == {"anime", "literature", "manhwa"}
    assert science.evidence.is_cross_domain
    assert science.evidence.domain_count == 3
    # Cross-domain is an attribute of the one insight, not a second card.
    assert len([i for i in result.insights if i.pattern_keys == ("science-fiction",)]) == 1


async def test_case_ab_positive_and_negative_coexist(
    db_session: AsyncSession, evaluation
) -> None:
    """AB: both directions representable, with no forced symmetry (case T)."""
    result = await insights_for(db_session, evaluation, "T")

    keys = {i.presentation_key for i in result.insights}
    assert PRESENTATION_ENJOYS_FEATURE in keys
    assert PRESENTATION_NEGATIVE_FEATURE in keys

    opposing = result.of_type(OBSERVATION_OPPOSING)
    assert len(opposing) == 1
    assert opposing[0].presentation_key == PRESENTATION_MIXED_DIRECTIONS
    # It is about the profile, so it carries no evidence of its own -- the
    # evidence is the insights it names.
    assert opposing[0].evidence is None
    assert len(opposing[0].pattern_keys) == 2


async def test_case_ac_emerging_is_secondary(
    db_session: AsyncSession, evaluation
) -> None:
    """AC: emerging cannot replace or outrank established evidence (case U)."""
    result = await insights_for(db_session, evaluation, "U")

    statuses = [
        i.evidence.status for i in result.insights if i.evidence is not None
    ]
    assert STATUS_ESTABLISHED in statuses
    assert STATUS_EMERGING in statuses
    first_emerging = statuses.index(STATUS_EMERGING)
    assert all(s == STATUS_ESTABLISHED for s in statuses[:first_emerging])
    assert all(s == STATUS_EMERGING for s in statuses[first_emerging:])


async def test_case_ad_reconsumption_stays_separate(
    db_session: AsyncSession, evaluation
) -> None:
    """AD: a work finished three times is not three ratings (case E)."""
    result = await insights_for(db_session, evaluation, "E")

    assert result.insights
    insight = next(i for i in result.insights if i.evidence.works_reconsumed > 0)
    evidence = insight.evidence
    assert evidence.total_completions > evidence.works_rated
    assert evidence.reconsumption_signal > 0.0
    # Both facts, side by side, neither multiplied into the other.
    assert evidence.works_rated == 2
    assert evidence.preference_evidence is not None


async def test_case_ae_abandonment_creates_no_negative_insight(
    db_session: AsyncSession, evaluation
) -> None:
    """AE: abandoning something unrated is not a dislike (case D)."""
    result = await insights_for(db_session, evaluation, "D")

    assert result.insights == []


async def test_case_af_no_ratings_no_preference_insights(
    db_session: AsyncSession, evaluation
) -> None:
    """AF: exposure and completion alone produce nothing (case C)."""
    result = await insights_for(db_session, evaluation, "C")

    assert result.insights == []
    assert result.diagnostics.key_patterns == 0


async def test_case_ag_a_fictional_theme_is_not_biography(
    db_session: AsyncSession, evaluation
) -> None:
    """AG: four highly rated works about found family say nothing about family.

    The layer is even more cautious than the case asked for. All four works
    carry `found-family`, and the same four carry `adventure`, `drama`,
    `existential-questioning` and `tragedy`, so the evidence cannot single
    out which of them the reader responded to. `found-family` therefore
    arrives as one of several indistinguishable alternatives rather than as a
    claim -- and a theme that cannot even be isolated as a media preference is
    a long way from being evidence about someone's family.
    """
    result = await insights_for(db_session, evaluation, "AG")

    carrying = [
        insight
        for insight in result.insights
        if "found-family" in (*insight.pattern_keys, *insight.indistinguishable_from)
    ]
    assert len(carrying) == 1
    insight = carrying[0]
    assert "found-family" in insight.indistinguishable_from
    assert insight.observation == OBSERVATION_PATTERN
    assert insight.presentation_key == PRESENTATION_ENJOYS_FEATURE
    # What it carries is media evidence and nothing else.
    assert insight.evidence.works_rated == 4
    assert insight.evidence.supporting_works
    assert insight.evidence.direction == "positive"


async def test_case_ah_no_account_of_why(
    db_session: AsyncSession, evaluation
) -> None:
    """AH: shared features, and no claim about what produced the ratings."""
    result = await insights_for(db_session, evaluation, "AH")

    assert result.insights
    for insight in result.insights:
        assert insight.presentation_key in PRESENTATION_KEYS
        assert insight.observation in OBSERVATIONS


async def test_case_ai_redundant_patterns_yield_no_duplicate_insights(
    db_session: AsyncSession, evaluation
) -> None:
    """AI: ten patterns on the identical works are one finding (case W)."""
    result = await insights_for(db_session, evaluation, "W")

    highlights = result.of_type(OBSERVATION_PATTERN)
    assert len(highlights) == 1
    # The alternatives travel with it rather than becoming their own insights.
    assert len(highlights[0].indistinguishable_from) >= 7
    keys = [i.pattern_keys for i in result.insights]
    assert len(set(keys)) == len(keys)


async def test_case_aj_fewer_insights_rather_than_padding(
    db_session: AsyncSession, evaluation
) -> None:
    """AJ: two supported findings produce two insights (case Q)."""
    result = await insights_for(db_session, evaluation, "Q")

    assert len(result.of_type(OBSERVATION_PATTERN)) == 2
    assert result.diagnostics.pattern_insights_capped == 0


async def test_one_observation_is_never_split_into_several_insights(
    db_session: AsyncSession, evaluation
) -> None:
    """Section 9, across every case with a profile."""
    for case in ("A", "G", "K", "M", "P", "R", "S", "U", "V", "AG"):
        result = await insights_for(db_session, evaluation, case)
        pattern_level = [
            i for i in result.insights if i.observation != OBSERVATION_OPPOSING
        ]
        keys = [i.pattern_keys for i in pattern_level]
        assert len(set(keys)) == len(keys), case


# --- what the representation cannot say -------------------------------------


def test_no_field_can_carry_a_cause() -> None:
    """Section 12, enforced on the dataclasses themselves."""
    names = {
        f.name
        for cls in (TasteInsight, InsightEvidence, InsightWork, ProfileInsights)
        for f in fields(cls)
    }
    for forbidden in (
        "why",
        "because",
        "caused_by",
        "cause",
        "reason",
        "motivation",
        "explanation",
        "inferred",
        "driver",
        "origin",
    ):
        assert not any(forbidden in name for name in names), forbidden


def test_no_field_can_carry_biography_or_psychology() -> None:
    """Section 13, enforced the same way."""
    names = {
        f.name
        for cls in (TasteInsight, InsightEvidence, InsightWork, ProfileInsights)
        for f in fields(cls)
    }
    for forbidden in (
        "childhood",
        "family_history",
        "relationship",
        "trauma",
        "mental",
        "health",
        "personality",
        "trait",
        "identity_of",
        "intelligence",
        "morality",
        "diagnosis",
        "biography",
        "experience",
    ):
        assert not any(forbidden in name for name in names), forbidden


def test_the_controlled_vocabularies_make_no_claim_about_a_person() -> None:
    """The keys are about media preference, and are checked to stay that way."""
    vocabulary = " ".join((*OBSERVATIONS, *PRESENTATION_KEYS)).lower()
    for forbidden in (
        "personality",
        "trait",
        "introvert",
        "extrovert",
        "openness",
        "neurotic",
        "trauma",
        "attachment",
        "because",
        "why",
        "diagnos",
        "recommend",
        "compatib",
        "score",
    ):
        assert forbidden not in vocabulary, forbidden


def test_the_module_reads_no_corpus_frequency_and_invents_no_score() -> None:
    """Section 3: no second opaque scalar, and Phase 1Q stays rejected."""
    import ast
    import pathlib

    source = pathlib.Path("app/services/preference/insights.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.endswith(("frequency", "experiment")) for name in imported
    ), imported

    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    for forbidden in (
        "idf",
        "document_frequency",
        "rarity",
        "novelty",
        "importance",
        "taste_score",
        "popularity",
        "compatibility",
    ):
        assert not any(forbidden in name.lower() for name in identifiers), forbidden


async def test_nothing_is_written(db_session: AsyncSession, evaluation) -> None:
    await db_session.flush()
    await insights_for(db_session, evaluation, "R")

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


def test_a_combination_insight_keeps_two_features_and_no_more() -> None:
    """Section 8: pairs only, and never rediscovered here."""
    pair = pattern(("a", "b"), (work(), work(), work(), work()))

    result = derive_insights(composed([pair]))

    insight = result.insights[0]
    assert insight.observation == OBSERVATION_COMBINATION
    assert len(insight.features) == 2
    assert insight.presentation_key == PRESENTATION_ENJOYS_COMBINATION


def test_an_emerging_combination_is_labelled_as_both() -> None:
    pair = pattern(
        ("a", "b"), (work(), work(), work()), status=STATUS_EMERGING, confidence=0.4
    )

    result = derive_insights(composed([], [pair]))

    insight = result.insights[0]
    assert insight.observation == OBSERVATION_EMERGING
    assert insight.presentation_key == PRESENTATION_EMERGING_COMBINATION


def test_opposing_directions_is_derived_from_what_is_shown() -> None:
    """An observation about a profile must be true of the profile shown.

    The negative pattern here is cut by the cap, so the contrast it would
    have created is not claimed.
    """
    positives = [
        pattern((f"p{index}",), (work(), work(), work()), confidence=0.9 - index / 100)
        for index in range(MAX_PATTERN_INSIGHTS)
    ]
    negative = pattern(("n",), (work(2), work(3), work(2)), direction="negative", confidence=0.1)

    result = derive_insights(composed([*positives, negative]))

    assert result.diagnostics.pattern_insights_capped == 1
    assert result.of_type(OBSERVATION_OPPOSING) == []
