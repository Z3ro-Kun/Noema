"""Phase 1T: selecting a small profile from everything Phase 1S discovered.

Two kinds of test, for the two ways this could go wrong.

First, that selection quietly becomes a second opinion about the evidence.
Guarded by asserting the selected entries hold the *same objects* aggregation
produced -- identity, not equality -- and that nothing appears in a profile
that aggregation did not establish.

Second, that a reader is shown the same finding several times under different
names, or shown fewer findings than their history supports. Guarded by cases
O through W, and by unit tests over constructed patterns for the rules the
17-work corpus cannot currently reach.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.profile import (
    MAX_KEY_PATTERNS,
    SelectedPattern,
    REJECTED_INDISTINGUISHABLE,
    REJECTED_NOT_ESTABLISHED,
    REJECTED_SECTION_FULL,
    SelectionDiagnostics,
    compose_from_patterns,
    compose_profile,
    rated_support,
)
from app.services.preference.taste import (
    KIND_COMBINATION,
    KIND_INDIVIDUAL,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    Feature,
    SupportingWork,
    TastePattern,
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
    return await compose_profile(session, evaluation[case].user_id)


# --- constructed patterns, for rules the corpus cannot reach ---------------


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


def test_the_cap_holds_and_the_overflow_is_recorded() -> None:
    works = [(work(), work(), work()) for _ in range(12)]
    patterns = [
        pattern((f"feature-{index:02d}",), group, confidence=0.9 - index / 100)
        for index, group in enumerate(works)
    ]

    composed = compose_from_patterns(uuid.uuid4(), patterns)

    assert len(composed.key_patterns) == MAX_KEY_PATTERNS
    assert composed.diagnostics.rejected[REJECTED_SECTION_FULL] == 4
    # Highest confidence first, and nothing invented to fill the section.
    assert composed.keys()[0] == "feature-00"


def test_a_profile_is_never_made_entirely_of_pairs() -> None:
    """Section 5's guarantee, on a shape the corpus cannot currently produce.

    More pairs than the section holds, all better supported than the one
    plain feature, so the individual is cut for space and the guarantee has
    to put it back.
    """
    pairs = [
        pattern(("a", f"b{index}"), (work(), work(), work(), work()), confidence=0.9)
        for index in range(MAX_KEY_PATTERNS + 1)
    ]
    individual = pattern(("plain",), (work(), work(), work()), confidence=0.4)

    composed = compose_from_patterns(uuid.uuid4(), [*pairs, individual])

    assert composed.diagnostics.individual_guarantee_applied
    kinds = {item.pattern.kind for item in composed.key_patterns}
    assert KIND_INDIVIDUAL in kinds
    assert KIND_COMBINATION in kinds


def test_the_guarantee_does_not_take_the_only_slot() -> None:
    """With one slot there is nothing to balance, and the better-supported wins."""
    pair = pattern(("a", "b"), (work(), work(), work(), work()), confidence=0.9)
    individual = pattern(("plain",), (work(), work(), work()), confidence=0.4)

    composed = compose_from_patterns(
        uuid.uuid4(), [pair, individual], max_key_patterns=1
    )

    assert composed.keys() == ["a+b"]
    assert not composed.diagnostics.individual_guarantee_applied


def test_patterns_on_identical_rated_works_collapse_to_one_entry() -> None:
    shared = (work(), work(), work())
    patterns = [pattern((key,), shared) for key in ("alpha", "beta", "gamma")]

    composed = compose_from_patterns(uuid.uuid4(), patterns)

    assert len(composed.key_patterns) == 1
    assert composed.key_patterns[0].indistinguishable_from == ("beta", "gamma")
    assert composed.diagnostics.rejected[REJECTED_INDISTINGUISHABLE] == 2


def test_one_differing_work_is_enough_to_stay_separate() -> None:
    """The rule stops at *identical*; overlap alone is not redundancy."""
    a, b, c, d = work(), work(), work(), work()
    patterns = [pattern(("alpha",), (a, b, c)), pattern(("beta",), (a, b, d))]

    composed = compose_from_patterns(uuid.uuid4(), patterns)

    assert len(composed.key_patterns) == 2
    assert all(not item.has_alternatives for item in composed.key_patterns)


def test_unrated_works_do_not_make_two_patterns_distinguishable() -> None:
    """Exposure carries no direction, so it cannot separate two findings."""
    rated = (work(), work(), work())
    patterns = [
        pattern(("alpha",), rated),
        pattern(("beta",), (*rated, work(rating=None))),
    ]

    composed = compose_from_patterns(uuid.uuid4(), patterns)

    assert len(composed.key_patterns) == 1
    assert rated_support(patterns[0]) == rated_support(patterns[1])


def test_a_combination_represents_a_group_it_shares_works_with() -> None:
    """Specificity where the evidence supports it, per section 18."""
    shared = (work(), work(), work(), work())
    patterns = [pattern(("plain",), shared), pattern(("a", "b"), shared)]

    composed = compose_from_patterns(uuid.uuid4(), patterns)

    assert composed.keys() == ["a+b"]
    assert composed.key_patterns[0].indistinguishable_from == ("plain",)


def test_selection_is_deterministic() -> None:
    works = [(work(), work(), work()) for _ in range(6)]
    patterns = [pattern((f"f{i}",), g) for i, g in enumerate(works)]

    first = compose_from_patterns(uuid.uuid4(), patterns)
    second = compose_from_patterns(uuid.uuid4(), list(reversed(patterns)))

    assert first.keys() == second.keys()


# --- the two critical guarantees -------------------------------------------


@pytest.mark.parametrize("case", ["A", "G", "K", "M", "P", "R", "S", "W"])
async def test_nothing_is_fabricated(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """Section 21: every selected pattern came from the aggregation layer."""
    user_id = evaluation[case].user_id
    aggregated = await build_taste_profile(db_session, user_id)
    # Composed from *this* pool, so identity is meaningful: a fresh query
    # would build equal-but-distinct objects and prove nothing.
    composed = compose_from_patterns(user_id, aggregated.patterns)

    eligible = [p for p in aggregated.patterns if p.status == STATUS_ESTABLISHED]
    assert len(composed.key_patterns) <= len(eligible)

    known = {id(p) for p in aggregated.patterns}
    for item in [*composed.key_patterns, *composed.early_signals]:
        assert id(item.pattern) in known, item.key


@pytest.mark.parametrize("case", ["A", "B", "G", "M", "R", "S", "T"])
async def test_selection_mutates_nothing(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """Section 22: selection reorders and groups. It does not touch values."""
    user_id = evaluation[case].user_id
    aggregated = await build_taste_profile(db_session, user_id)
    before = {
        p.key: (
            p.preference_evidence,
            p.confidence,
            p.direction,
            p.domains,
            tuple(w.work_id for w in p.supporting_works),
            p.reconsumption_signal,
            p.abandonment_signal,
            p.works_reconsumed,
            p.total_completions,
        )
        for p in aggregated.patterns
    }

    composed = compose_from_patterns(user_id, aggregated.patterns)

    after = {
        p.key: (
            p.preference_evidence,
            p.confidence,
            p.direction,
            p.domains,
            tuple(w.work_id for w in p.supporting_works),
            p.reconsumption_signal,
            p.abandonment_signal,
            p.works_reconsumed,
            p.total_completions,
        )
        for p in aggregated.patterns
    }
    assert before == after
    # And the selected entries are the very objects, not copies of them.
    by_key = {p.key: p for p in aggregated.patterns}
    for item in composed.key_patterns:
        assert item.pattern is by_key[item.key]


# --- cases A-N still behave ------------------------------------------------


@pytest.mark.parametrize("case", ["C", "D"])
async def test_no_ratings_means_no_profile(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    composed = await profile_for(db_session, evaluation, case)

    assert composed.key_patterns == []
    assert composed.early_signals == []


async def test_case_a_and_b_select_the_same_shape_in_opposite_directions(
    db_session: AsyncSession, evaluation
) -> None:
    positive = await profile_for(db_session, evaluation, "A")
    negative = await profile_for(db_session, evaluation, "B")

    assert positive.keys()[0] == "psychological-depth"
    assert negative.keys()[0] == "psychological-depth"
    assert positive.key_patterns[0].pattern.direction == "positive"
    assert negative.key_patterns[0].pattern.direction == "negative"


async def test_mixed_history_selects_nothing_for_the_key_section(
    db_session: AsyncSession, evaluation
) -> None:
    """Case F: emerging evidence is real, and is not promoted to fill a page."""
    composed = await profile_for(db_session, evaluation, "F")

    assert composed.key_patterns == []
    assert composed.early_signals


# --- cases O-W: selection ---------------------------------------------------


async def test_case_o_collapses_a_redundant_pool(
    db_session: AsyncSession, evaluation
) -> None:
    composed = await profile_for(db_session, evaluation, "O")
    diagnostics = composed.diagnostics

    assert diagnostics.eligible >= 12
    assert len(composed.key_patterns) <= 5
    assert diagnostics.rejected[REJECTED_INDISTINGUISHABLE] > 0
    assert diagnostics.indistinguishable_groups


async def test_case_p_keeps_complementary_patterns_apart(
    db_session: AsyncSession, evaluation
) -> None:
    """Different rated works are different findings, even sharing a feature."""
    composed = await profile_for(db_session, evaluation, "P")

    assert len(composed.key_patterns) >= 5
    supports = [rated_support(item.pattern) for item in composed.key_patterns]
    assert len(set(supports)) == len(supports)
    # Genuinely different evidence, not the same works relabelled.
    assert len({frozenset(s) for s in supports}) > 1


async def test_case_q_returns_fewer_than_five_without_padding(
    db_session: AsyncSession, evaluation
) -> None:
    composed = await profile_for(db_session, evaluation, "Q")

    assert 0 < len(composed.key_patterns) < 5
    assert len(composed.key_patterns) == composed.diagnostics.distinct_support_sets


async def test_case_r_caps_a_rich_history(
    db_session: AsyncSession, evaluation
) -> None:
    composed = await profile_for(db_session, evaluation, "R")

    assert composed.diagnostics.distinct_support_sets > MAX_KEY_PATTERNS
    assert len(composed.key_patterns) == MAX_KEY_PATTERNS
    assert composed.diagnostics.rejected[REJECTED_SECTION_FULL] > 0


async def test_case_s_surfaces_an_established_combination(
    db_session: AsyncSession, evaluation
) -> None:
    """Rare on this corpus, and it must not crowd out individual features."""
    composed = await profile_for(db_session, evaluation, "S")

    kinds = [item.pattern.kind for item in composed.key_patterns]
    assert KIND_COMBINATION in kinds
    assert KIND_INDIVIDUAL in kinds
    combination = next(
        item for item in composed.key_patterns if item.pattern.kind == KIND_COMBINATION
    )
    assert combination.key == "crime-and-investigation+urban-modernity"
    assert combination.pattern.status == STATUS_ESTABLISHED


async def test_case_t_selects_a_negative_pattern_beside_a_positive_one(
    db_session: AsyncSession, evaluation
) -> None:
    composed = await profile_for(db_session, evaluation, "T")

    directions = {item.pattern.direction for item in composed.key_patterns}
    assert directions == {"positive", "negative"}


async def test_case_u_prefers_established_over_emerging(
    db_session: AsyncSession, evaluation
) -> None:
    composed = await profile_for(db_session, evaluation, "U")

    assert composed.key_patterns
    assert all(
        item.pattern.status == STATUS_ESTABLISHED for item in composed.key_patterns
    )
    assert all(
        item.pattern.status == STATUS_EMERGING for item in composed.early_signals
    )
    # Evidence magnitude does not buy a place in the key section: an early
    # signal matches or beats the weakest selected pattern and still stays
    # out, because status decides and magnitude does not.
    strongest_early = max(
        (abs(i.pattern.preference_evidence or 0) for i in composed.early_signals),
        default=0.0,
    )
    weakest_key = min(
        abs(i.pattern.preference_evidence or 0) for i in composed.key_patterns
    )
    assert strongest_early >= weakest_key
    # And it is genuinely less supported, which is why it waits.
    thinnest_key = min(i.pattern.works_rated for i in composed.key_patterns)
    assert all(
        i.pattern.works_rated < thinnest_key for i in composed.early_signals
    )


async def test_case_v_keeps_domain_provenance_through_selection(
    db_session: AsyncSession, evaluation
) -> None:
    composed = await profile_for(db_session, evaluation, "V")
    science = next(
        item for item in composed.key_patterns if item.key == "science-fiction"
    )

    assert set(science.pattern.domains) == {"anime", "literature", "manhwa"}


async def test_case_v_does_not_rank_breadth_above_support(
    db_session: AsyncSession, evaluation
) -> None:
    """Domain count is provenance, never a quality term."""
    composed = await profile_for(db_session, evaluation, "V")
    ordered = [
        (-item.pattern.confidence, abs(item.pattern.preference_evidence or 0), item.key)
        for item in composed.key_patterns
    ]

    assert [x[0] for x in ordered] == sorted(x[0] for x in ordered)


async def test_case_w_reports_alternatives_instead_of_repeating_itself(
    db_session: AsyncSession, evaluation
) -> None:
    """Ten established patterns on the identical three works."""
    composed = await profile_for(db_session, evaluation, "W")

    assert composed.diagnostics.eligible >= 8
    assert len(composed.key_patterns) == 1
    entry = composed.key_patterns[0]
    assert len(entry.indistinguishable_from) >= 7


# --- what the layer refuses to do ------------------------------------------


async def test_emerging_never_reaches_the_key_section(
    db_session: AsyncSession, evaluation
) -> None:
    for case in ("A", "H", "K", "P", "S", "U"):
        composed = await profile_for(db_session, evaluation, case)
        assert all(
            item.pattern.status == STATUS_ESTABLISHED
            for item in composed.key_patterns
        ), case
        assert composed.diagnostics.rejected.get(REJECTED_NOT_ESTABLISHED, 0) >= 0


def test_the_representation_carries_no_personal_or_causal_vocabulary() -> None:
    from dataclasses import fields

    from app.services.preference.profile import ComposedProfile

    names = {
        f.name
        for cls in (ComposedProfile, SelectedPattern, SelectionDiagnostics)
        for f in fields(cls)
    }
    assert names
    for forbidden in (
        "personality",
        "trait",
        "because",
        "cause",
        "reason",
        "explanation",
        "recommendation",
        "score",
        "biography",
    ):
        assert not any(forbidden in name for name in names), forbidden


def test_selection_reads_no_corpus_frequency() -> None:
    """Phase 1Q's rejection holds here too."""
    import ast
    import pathlib

    source = pathlib.Path("app/services/preference/profile.py").read_text(
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
    for forbidden in ("idf", "specificity", "salience", "document_frequency", "rarity"):
        assert not any(forbidden in name.lower() for name in identifiers), forbidden


async def test_nothing_is_written(db_session: AsyncSession, evaluation) -> None:
    await db_session.flush()
    await profile_for(db_session, evaluation, "R")

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
