"""Phase 1S: taste aggregation over Phase 1O's evidence.

The tests are organised around the two things that could go wrong. First,
that the aggregation quietly becomes a second rating system -- guarded by
asserting a one-feature pattern is numerically identical to the concept's own
`ConceptEvidence`, field by field. Second, that a single rating on a
many-concept work turns into a page of confident patterns -- guarded by cases
J through N, which were added for exactly that.

Nothing here asserts that a pattern is *true*. It asserts what the evidence
does and does not permit Noema to say.
"""

from statistics import fmean, pstdev

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.evidence import build_preference_profile
from app.services.preference.taste import (
    DEFAULT_TASTE_PARAMETERS,
    FEATURE_FAMILIES,
    KIND_COMBINATION,
    KIND_INDIVIDUAL,
    REJECTED_MIN_SUPPORT,
    REJECTED_NOT_DISTINCT,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    STATUS_INSUFFICIENT,
    build_taste_profile,
)
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


async def profile_for(session, evaluation, case: str):
    return await build_taste_profile(session, evaluation[case].user_id)


# --- the load-bearing identity ---------------------------------------------


async def test_a_single_feature_pattern_is_the_concept_evidence_exactly(
    db_session: AsyncSession, evaluation
) -> None:
    """The guarantee the whole layer rests on.

    If aggregation ever computed its own evidence, direction or confidence,
    these would drift. They are produced by Phase 1O's own finaliser, so they
    cannot.
    """
    user_id = evaluation["A"].user_id
    baseline = {
        item.concept_slug: item
        for item in (await build_preference_profile(db_session, user_id)).concepts
    }
    taste = await build_taste_profile(db_session, user_id)

    checked = 0
    for pattern in taste.individual():
        concept = baseline[pattern.features[0].key]
        assert pattern.preference_evidence == concept.preference_evidence
        assert pattern.direction == concept.direction
        assert pattern.confidence == concept.confidence
        assert pattern.works_rated == concept.works_rated
        assert pattern.works_exposed == concept.works_exposed
        assert pattern.works_completed == concept.works_completed
        assert pattern.works_abandoned == concept.works_abandoned
        assert pattern.works_on_hold == concept.works_on_hold
        assert pattern.total_completions == concept.total_completions
        assert pattern.reconsumption_signal == concept.reconsumption_signal
        assert pattern.abandonment_signal == concept.abandonment_signal
        checked += 1

    assert checked >= 10


async def test_the_aggregation_is_deterministic(
    db_session: AsyncSession, evaluation
) -> None:
    first = await profile_for(db_session, evaluation, "H")
    second = await profile_for(db_session, evaluation, "H")

    assert [p.key for p in first.patterns] == [p.key for p in second.patterns]
    assert [
        (p.status, p.direction, p.preference_evidence, p.confidence)
        for p in first.patterns
    ] == [
        (p.status, p.direction, p.preference_evidence, p.confidence)
        for p in second.patterns
    ]


async def test_nothing_is_written(db_session: AsyncSession, evaluation) -> None:
    """A derived view, not a stored entity."""
    await db_session.flush()
    await profile_for(db_session, evaluation, "A")

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


# --- structure -------------------------------------------------------------


async def test_patterns_are_individuals_or_pairs_and_never_larger(
    db_session: AsyncSession, evaluation
) -> None:
    """Specificity is earned by evidence; three-way patterns are not built."""
    for case in ("A", "H", "J", "L"):
        taste = await profile_for(db_session, evaluation, case)
        for pattern in taste.patterns:
            assert 1 <= len(pattern.features) <= 2, (case, pattern.key)
            expected = KIND_INDIVIDUAL if len(pattern.features) == 1 else KIND_COMBINATION
            assert pattern.kind == expected


async def test_every_feature_belongs_to_a_declared_family(
    db_session: AsyncSession, evaluation
) -> None:
    taste = await profile_for(db_session, evaluation, "A")

    assert taste.patterns
    for pattern in taste.patterns:
        for feature in pattern.features:
            assert feature.family in FEATURE_FAMILIES


async def test_insufficient_patterns_never_reach_the_profile(
    db_session: AsyncSession, evaluation
) -> None:
    taste = await profile_for(db_session, evaluation, "H")

    assert taste.patterns
    assert all(p.status != STATUS_INSUFFICIENT for p in taste.patterns)
    # They were considered, and counted, rather than never generated.
    assert taste.diagnostics.individual_below_support > 0


async def test_a_pattern_at_exactly_the_minimum_is_only_emerging(
    db_session: AsyncSession, evaluation
) -> None:
    """Clearing the bar that allows discussion is not the same as being established."""
    minimum = DEFAULT_TASTE_PARAMETERS.minimum_individual_rated
    taste = await profile_for(db_session, evaluation, "A")

    at_minimum = [p for p in taste.individual() if p.works_rated == minimum]
    assert at_minimum
    assert all(p.status == STATUS_EMERGING for p in at_minimum)


async def test_domains_are_provenance_and_do_not_order_patterns(
    db_session: AsyncSession, evaluation
) -> None:
    """Cross-medium support is recorded, never rewarded.

    Phase 1S keeps domain provenance so a later phase can tell "psychological
    across three media" from "psychological in anime only". It must not act
    on it: ordering is status, then kind, then the Phase 1R keys.
    """
    inversions = 0
    for case in ("A", "G", "H", "J"):
        taste = await profile_for(db_session, evaluation, case)
        assert taste.patterns

        expected = sorted(
            taste.patterns,
            key=lambda p: (
                {"established": 0, "emerging": 1, "insufficient": 2}[p.status],
                0 if p.kind == KIND_INDIVIDUAL else 1,
                -p.confidence,
                -abs(p.preference_evidence or 0.0),
                p.key,
            ),
        )
        assert [p.key for p in taste.patterns] == [p.key for p in expected], case

        # Somewhere a narrower-provenance pattern outranks a broader one. If
        # domain count were an ordering term, this could not happen.
        counts = [p.domain_count for p in taste.patterns]
        inversions += sum(
            1 for before, after in zip(counts, counts[1:]) if before < after
        )

    assert inversions > 0, "domain count never inverts; the guard proves nothing"


# --- cases A-I: the Phase 1N boundary still holds ---------------------------


async def test_case_a_recovers_the_documented_feature(
    db_session: AsyncSession, evaluation
) -> None:
    taste = await profile_for(db_session, evaluation, "A")
    pattern = taste.by_key()["psychological-depth"]

    assert pattern.status == STATUS_ESTABLISHED
    assert pattern.direction == "positive"
    assert pattern.works_rated == 5
    # Concept-level, not medium-level: the same ratings span two domains.
    assert set(pattern.domains) == {"anime", "literature"}


async def test_case_b_reverses_the_same_exposure(
    db_session: AsyncSession, evaluation
) -> None:
    """Identical works, opposite ratings. The dataset's load-bearing pair."""
    positive = (await profile_for(db_session, evaluation, "A")).by_key()[
        "psychological-depth"
    ]
    negative = (await profile_for(db_session, evaluation, "B")).by_key()[
        "psychological-depth"
    ]

    assert positive.direction == "positive"
    assert negative.direction == "negative"
    assert positive.works_rated == negative.works_rated
    assert {w.title for w in positive.supporting_works} == {
        w.title for w in negative.supporting_works
    }


async def test_case_c_generates_no_pattern_from_exposure_alone(
    db_session: AsyncSession, evaluation
) -> None:
    """Completing something is not approving of it."""
    taste = await profile_for(db_session, evaluation, "C")

    assert taste.patterns == []
    assert taste.diagnostics.pairs_considered == 0
    assert taste.diagnostics.features_available > 0


async def test_case_d_invents_no_dislike_from_abandonment(
    db_session: AsyncSession, evaluation
) -> None:
    """Abandonment and on-hold are behaviour, never negative preference."""
    taste = await profile_for(db_session, evaluation, "D")

    assert taste.patterns == []


async def test_case_e_keeps_reconsumption_out_of_the_rating_evidence(
    db_session: AsyncSession, evaluation
) -> None:
    """Three completions of a work rated 9 is not three ratings of 9."""
    taste = await profile_for(db_session, evaluation, "E")
    pattern = taste.by_key()["crime-and-investigation"]

    rated = [
        w.normalized_rating
        for w in pattern.supporting_works
        if w.normalized_rating is not None
    ]
    assert pattern.works_rated == 2
    assert pattern.total_completions > pattern.works_completed
    assert pattern.reconsumption_signal > 0.0
    # The evidence is the mean of the ratings, untouched by how many times
    # the works were finished.
    assert pattern.preference_evidence == pytest.approx(fmean(rated), abs=1e-6)


async def test_case_f_invents_no_preference_from_mixed_ratings(
    db_session: AsyncSession, evaluation
) -> None:
    """The honest output for an inconsistent history is no settled pattern."""
    taste = await profile_for(db_session, evaluation, "F")

    assert taste.patterns
    assert taste.established() == []


async def test_case_g_preserves_cross_domain_support(
    db_session: AsyncSession, evaluation
) -> None:
    taste = await profile_for(db_session, evaluation, "G")
    pattern = taste.by_key()["science-fiction"]

    assert pattern.status == STATUS_ESTABLISHED
    assert set(pattern.domains) == {"anime", "literature", "manhwa"}
    assert pattern.domain_count == 3


async def test_case_h_reads_ratings_against_the_users_own_distribution(
    db_session: AsyncSession, evaluation
) -> None:
    """A 7 from someone whose maximum is 7 is a high rating."""
    taste = await profile_for(db_session, evaluation, "H")
    pattern = taste.by_key()["science-fiction"]

    assert max(w.rating for w in pattern.supporting_works if w.rating) <= 7
    assert pattern.direction == "positive"
    assert pattern.status == STATUS_ESTABLISHED


async def test_case_i_keeps_evidence_from_removed_works(
    db_session: AsyncSession, evaluation
) -> None:
    taste = await profile_for(db_session, evaluation, "I")

    assert taste.patterns
    pattern = taste.by_key()["science-fiction"]
    assert all(w.in_library is False for w in pattern.supporting_works)
    assert pattern.direction == "positive"


# --- cases J-N: combinations ------------------------------------------------


async def test_case_j_discovers_a_combination_that_earned_its_place(
    db_session: AsyncSession, evaluation
) -> None:
    """The one shape in which a pair is a real finding.

    Both constituents also appear in works this user rated poorly, so the
    three works carrying both are genuinely a different population.
    """
    taste = await profile_for(db_session, evaluation, "J")
    pattern = taste.by_key()["crime-and-investigation+mystery"]

    assert pattern.kind == KIND_COMBINATION
    assert pattern.direction == "positive"
    assert pattern.works_rated == 3
    assert not pattern.is_ambiguous

    # Strictly more selective than both parts.
    individuals = taste.by_key()
    for feature in pattern.features:
        assert pattern.works_rated < individuals[feature.key].works_rated

    # And it says something neither part says on its own.
    distinction = DEFAULT_TASTE_PARAMETERS.combination_distinction()
    for part in pattern.constituent_evidence:
        assert abs(pattern.preference_evidence - part) >= distinction


async def test_case_k_refuses_a_combination_seen_once(
    db_session: AsyncSession, evaluation
) -> None:
    """One 10/10 does not name which pair of its concepts earned it."""
    taste = await profile_for(db_session, evaluation, "K")

    assert taste.combinations() == []
    assert taste.diagnostics.pairs_considered > 200
    assert taste.diagnostics.pairs_rejected[REJECTED_MIN_SUPPORT] > 0
    # The single-occurrence pair the case is built around.
    assert "scientific-overreach+time-manipulation" not in taste.by_key()


async def test_case_l_makes_no_confident_claim_about_a_conflicting_pair(
    db_session: AsyncSession, evaluation
) -> None:
    """The pair sits in this user's best and worst works alike."""
    taste = await profile_for(db_session, evaluation, "L")

    assert "psychological-depth+mystery" not in taste.by_key()
    assert taste.diagnostics.pairs_rejected[REJECTED_NOT_DISTINCT] > 0

    # Rejected because the disagreement leaves it saying nothing its parts do
    # not: verify the underlying ratings really do conflict.
    baseline = {
        item.concept_slug: item
        for item in (
            await build_preference_profile(db_session, evaluation["L"].user_id)
        ).concepts
    }
    shared = {
        c.work_id for c in baseline["psychological-depth"].contributions if c.rating
    } & {c.work_id for c in baseline["mystery"].contributions if c.rating}
    normalized = [
        c.normalized_rating
        for c in baseline["psychological-depth"].contributions
        if c.work_id in shared and c.normalized_rating is not None
    ]
    assert len(normalized) == 4
    assert pstdev(normalized) > 0.5

    assert all(
        p.status != STATUS_ESTABLISHED or p.direction != "positive"
        for p in taste.combinations()
    )


async def test_case_m_does_not_suppress_a_common_concept(
    db_session: AsyncSession, evaluation
) -> None:
    """Phase 1Q's conclusion, enforced.

    `tragedy` sits on eleven of seventeen corpus works. That is not a reason
    to discount what this user's own ratings say about it.
    """
    taste = await profile_for(db_session, evaluation, "M")
    pattern = taste.by_key()["tragedy"]

    assert pattern.status == STATUS_ESTABLISHED
    assert pattern.direction == "positive"
    assert pattern.works_rated == 4


async def test_case_n_turns_one_rating_into_no_patterns(
    db_session: AsyncSession, evaluation
) -> None:
    """Eighteen concepts on one work offer 153 pairs and must yield none."""
    taste = await profile_for(db_session, evaluation, "N")

    assert taste.diagnostics.pairs_considered == 153
    assert taste.diagnostics.pairs_admitted == 0
    assert taste.combinations() == []
    # The only feature with two rated works behind it, and only emerging.
    assert [p.key for p in taste.patterns] == ["fantasy"]
    assert taste.patterns[0].status == STATUS_EMERGING


# --- what the layer refuses to do ------------------------------------------


async def test_a_combination_is_never_inferred_from_its_parts(
    db_session: AsyncSession, evaluation
) -> None:
    """Two positive features do not make a positive pair.

    Case L has two positive constituents and no admitted pair between them;
    case J has two weaker constituents and a much stronger pair. Neither
    follows from the parts.
    """
    conflicting = await profile_for(db_session, evaluation, "L")
    individuals = conflicting.by_key()
    assert individuals["psychological-depth"].preference_evidence > 0
    assert individuals["mystery"].preference_evidence > 0
    assert "psychological-depth+mystery" not in conflicting.by_key()

    earned = (await profile_for(db_session, evaluation, "J")).by_key()[
        "crime-and-investigation+mystery"
    ]
    assert earned.preference_evidence > max(earned.constituent_evidence)


async def test_an_ambiguous_pair_is_never_established(
    db_session: AsyncSession, evaluation
) -> None:
    """Evidence that equally supports two claims establishes neither."""
    found = False
    for case in ("H", "J", "L"):
        taste = await profile_for(db_session, evaluation, case)
        for pattern in taste.combinations():
            if pattern.is_ambiguous:
                found = True
                assert pattern.status != STATUS_ESTABLISHED
                # The siblings it cannot be told apart from are named.
                assert pattern.shares_support_with
    assert found, "no ambiguous pair in the fixtures; the guard is untested"


async def test_a_pair_cannot_rest_on_a_feature_that_is_not_itself_a_pattern(
    db_session: AsyncSession, evaluation
) -> None:
    """The guard is unreachable at the default thresholds, and not dead.

    A pair's support is a subset of each part's, so with the combination
    minimum set at or above the individual one no part can be insufficient.
    Raise the individual minimum above it and the guard does its job.
    """
    from app.services.preference.taste import REJECTED_WEAK_PARTS, TasteParameters

    user_id = evaluation["H"].user_id
    default = await build_taste_profile(db_session, user_id)
    assert REJECTED_WEAK_PARTS not in default.diagnostics.pairs_rejected

    inverted = await build_taste_profile(
        db_session,
        user_id,
        taste=TasteParameters(
            minimum_individual_rated=4, minimum_combination_rated=3
        ),
    )
    assert inverted.diagnostics.pairs_rejected[REJECTED_WEAK_PARTS] > 0


async def test_unrated_works_never_generate_a_combination(
    db_session: AsyncSession, evaluation
) -> None:
    """Co-occurrence without a rating has no direction to discover."""
    for case in ("C", "D"):
        taste = await profile_for(db_session, evaluation, case)
        assert taste.diagnostics.pairs_considered == 0
        assert taste.combinations() == []


def test_the_aggregation_reads_no_corpus_frequency() -> None:
    """Phase 1Q's machinery stays rejected, stated where it cannot rot."""
    import ast
    import pathlib

    source = pathlib.Path("app/services/preference/taste.py").read_text(
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

    # Identifiers only. The prose above them is free to discuss why frequency
    # was rejected; the code may not read it.
    identifiers = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for forbidden in ("idf", "specificity", "salience", "document_frequency"):
        assert not any(forbidden in name.lower() for name in identifiers), forbidden


def test_the_representation_carries_no_personal_or_causal_vocabulary() -> None:
    """The hard boundary, enforced on the field names themselves."""
    from dataclasses import fields

    from app.services.preference.taste import TastePattern

    names = {f.name for f in fields(TastePattern)}
    for forbidden in (
        "personality",
        "trait",
        "because",
        "cause",
        "reason",
        "explanation",
        "score",
        "recommendation",
        "openness",
        "neuroticism",
        "introversion",
    ):
        assert not any(forbidden in name for name in names), forbidden
