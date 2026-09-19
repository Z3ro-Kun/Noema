"""Phase 1S: turning many preference signals into a few taste patterns.

The layer between Phase 1O's per-concept evidence and a Taste Profile that
does not exist yet. It answers one question -- *which features, alone or in
pairs, does this user's rating history actually support?* -- and deliberately
answers nothing else. No wording, no traits, no recommendations, no causes.

**It invents no preference mathematics.** Every number a pattern carries is
produced by `evidence._finalise`, the same function Phase 1O runs on a single
concept, applied to the works a pattern covers. A pattern over one feature is
therefore numerically identical to that concept's `ConceptEvidence`, and a
test asserts it field by field. That identity is the whole design: it is what
makes "the combination has its own evidence" true by construction rather than
by a second rating system that could drift from the first.

---

What a pattern may be built from

`work_concepts` is the only feature source, split by `Concept.concept_type`
into three families -- theme, genre, motif. Section B of the Phase 1S report
records what else was inspected and why it was left out; the short version is
that creator credits do not repeat often enough to evidence anything (178 of
212 creators appear on exactly one work), and format, year, country,
popularity and community score are either absent for literature or are
properties of the corpus rather than of the reader.

Domain is carried as **provenance, never as a feature**. A pattern records
which media its support came from so a later phase can tell "psychological
across three media" from "psychological in anime only", but domain never
enters a pattern's identity and never moves it up an ordering.

---

Why a single rating cannot name its own cause

A user rates Fullmetal Alchemist 10. The work carries eighteen concepts. That
one number says nothing about which of the eighteen earned it, and the 153
pairs those concepts generate are not 153 discoveries -- they are one
observation wearing 153 labels. Everything below exists to stop that from
becoming a taste profile:

  Individual features need two rated works, because one rating cannot be
  attributed among a work's concepts at all.

  Combinations need three, because two similar works co-occur on many pairs
  at once. Measured on the evaluation library, a two-work overlap produced
  eleven "patterns" describing the same two works.

  A combination must be strictly more selective than both its parts. If every
  work carrying A also carries B, then "A and B" and "A" describe the same
  works, and the pair has earned no specificity over the simpler claim.

  A combination must say something different from both its parts. If its
  evidence sits within the width of the engine's own neutral band of each
  constituent's, the engine already treats those values as indistinguishable,
  so the pair adds nothing.

  When several admitted pairs rest on the *identical* set of rated works,
  the evidence cannot tell them apart. They stay candidates, and they are
  flagged, but none of them may become established on evidence that equally
  supports its neighbours.

None of these rules reads corpus frequency. A concept on eleven of seventeen
works is treated exactly like a concept on one: what decides a pattern is the
user's own ratings. Phase 1Q's rejection of inverse-frequency weighting is
upheld here, and a test asserts no frequency-derived module is imported.

---

What this layer refuses to do

It reports that a pattern's works were rated well. It never reports why. It
carries no wording, because wording is where a media observation turns into a
claim about a person, and that turn belongs to a later phase that has been
designed for it -- not to an aggregation function.
"""

import itertools
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.evidence import (
    DIRECTION_NEUTRAL,
    DIRECTION_UNKNOWN,
    STATUS_ABANDONED,
    STATUS_ON_HOLD,
    ConceptEvidence,
    WorkContribution,
    build_preference_profile,
)

# Phase 1O's own finaliser. Imported rather than reimplemented on purpose:
# re-deriving evidence, direction, confidence and the behavioural signals here
# would create a second definition of what a rating means, free to drift from
# the authoritative one. It is private to signal "not a product API", and this
# module is a sibling inside the same package.
from app.services.preference.evidence import _finalise as phase1o_finalise
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters

# The feature families available today, which are exactly the values of
# `Concept.concept_type`. Listed rather than discovered so that a new
# concept type cannot silently start producing taste patterns.
FEATURE_FAMILIES = ("theme", "genre", "motif")

KIND_INDIVIDUAL = "individual"
KIND_COMBINATION = "combination"

# How much of a claim the evidence supports. Deliberately three coarse words
# and not a number: this is a statement about evidence sufficiency, and any
# scale here would be read as certainty about the person.
STATUS_INSUFFICIENT = "insufficient"
STATUS_EMERGING = "emerging"
STATUS_ESTABLISHED = "established"

# Why a candidate pair did not become a pattern. Counted and reported, so the
# discovery step can be inspected instead of trusted.
REJECTED_MIN_SUPPORT = "below_minimum_support"
REJECTED_NOT_SELECTIVE = "not_more_selective_than_parts"
REJECTED_NOT_DISTINCT = "evidence_matches_parts"
REJECTED_WEAK_PARTS = "constituent_lacks_support"


@dataclass(frozen=True)
class TasteParameters:
    """Thresholds for candidate generation, with their reasoning.

    Separate from `PreferenceParameters` because nothing here changes what a
    rating means -- these decide only how much evidence a claim needs before
    Noema is allowed to make it.
    """

    # One rating cannot be attributed among a work's concepts, so a feature
    # needs at least two rated works before it is a pattern at all.
    minimum_individual_rated: int = 2

    # Three, not two. Two similar works overlap on many pairs simultaneously,
    # and every one of those pairs looks equally well supported; three is the
    # smallest support that a single pair of similar works cannot manufacture.
    # It also coincides with `confidence_half_point`, where the engine's own
    # volume term reaches one half.
    minimum_combination_rated: int = 3

    # A combination must differ from each constituent by at least this much
    # to count as saying something new. Derived, not chosen: the engine
    # reports evidence within `neutral_band` of zero as no direction at all,
    # so that band -- which spans `2 * neutral_band` -- is the smallest
    # difference it already treats as meaningful. Two values closer than that
    # are two values the engine declines to distinguish.
    def combination_distinction(
        self, parameters: PreferenceParameters = DEFAULT_PARAMETERS
    ) -> float:
        return 2.0 * parameters.neutral_band


DEFAULT_TASTE_PARAMETERS = TasteParameters()


@dataclass(frozen=True)
class Feature:
    """One thing a pattern can be about."""

    key: str
    name: str
    family: str


@dataclass(frozen=True)
class SupportingWork:
    """One work behind a pattern, and what this user did with it.

    A trimmed `WorkContribution`: the per-concept annotation confidence and
    method are dropped, because a pattern spans features and a single
    annotation's confidence would be misleading beside it.
    """

    work_id: uuid.UUID
    title: str
    domain_slug: str
    status: str
    rating: int | None
    normalized_rating: float | None
    times_completed: int
    in_library: bool


@dataclass
class TastePattern:
    """One feature, or one pair, with everything the evidence says about it."""

    features: tuple[Feature, ...]
    kind: str
    status: str

    # --- Phase 1O's values, over this pattern's works. Not recomputed here.
    direction: str
    preference_evidence: float | None
    confidence: float

    # --- behaviour, reported beside the rating signal and never folded in.
    # A re-read of something rated 4 is more engagement, not more liking.
    exposure: float = 0.0
    engagement: float = 0.0
    reconsumption_signal: float = 0.0
    abandonment_signal: float = 0.0

    # --- what actually happened, in checkable counts.
    works_rated: int = 0
    works_exposed: int = 0
    works_completed: int = 0
    works_abandoned: int = 0
    works_on_hold: int = 0
    works_reconsumed: int = 0
    total_completions: int = 0

    # How many of the rated works sit above and below this reader's own
    # baseline. Carried through from `ConceptEvidence`, which already derives
    # them; nothing here recomputes a rating. Together they say whether the
    # ratings behind a pattern agree, which a product surface needs in order
    # to hedge honestly rather than by inspecting a confidence number.
    ratings_above_baseline: int = 0
    ratings_below_baseline: int = 0

    supporting_works: tuple[SupportingWork, ...] = ()
    # Provenance for the cross-medium question, never an input to anything.
    domains: tuple[str, ...] = ()

    # --- combination provenance -------------------------------------------
    # Each constituent's own evidence, so a reader of this structure can see
    # that the pair was not inferred from its parts.
    constituent_evidence: tuple[float | None, ...] = ()
    # Other admitted pairs resting on exactly these rated works. Non-empty
    # means the evidence cannot single this pair out from those.
    shares_support_with: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return "+".join(feature.key for feature in self.features)

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.shares_support_with)

    @property
    def domain_count(self) -> int:
        return len(self.domains)


@dataclass
class TasteDiagnostics:
    """What discovery considered, and why most of it was discarded."""

    features_available: int = 0
    individual_candidates: int = 0
    individual_below_support: int = 0
    pairs_considered: int = 0
    pairs_rejected: dict[str, int] = field(default_factory=dict)
    pairs_admitted: int = 0
    ambiguous_support_sets: int = 0


@dataclass
class TasteProfile:
    """A user's taste evidence: patterns, and how they were arrived at."""

    user_id: uuid.UUID
    patterns: list[TastePattern] = field(default_factory=list)
    diagnostics: TasteDiagnostics = field(default_factory=TasteDiagnostics)

    def by_key(self) -> dict[str, TastePattern]:
        return {pattern.key: pattern for pattern in self.patterns}

    def individual(self) -> list[TastePattern]:
        return [p for p in self.patterns if p.kind == KIND_INDIVIDUAL]

    def combinations(self) -> list[TastePattern]:
        return [p for p in self.patterns if p.kind == KIND_COMBINATION]

    def established(self) -> list[TastePattern]:
        return [p for p in self.patterns if p.status == STATUS_ESTABLISHED]


# --- building one pattern --------------------------------------------------


def _evidence_over(
    features: tuple[Feature, ...],
    contributions: list[WorkContribution],
    parameters: PreferenceParameters,
) -> ConceptEvidence:
    """Run Phase 1O's finaliser over one pattern's works.

    The counts below are re-derived from the contributions rather than copied,
    because a pattern's works are a subset of any one concept's. They are
    plain counting over facts; every interpretive step -- normalization,
    direction, confidence, saturation -- stays inside `phase1o_finalise`.
    """
    evidence = ConceptEvidence(
        concept_slug="+".join(feature.key for feature in features),
        concept_name=" + ".join(feature.name for feature in features),
        concept_type=features[0].family if len(features) == 1 else KIND_COMBINATION,
    )
    evidence.contributions = list(contributions)

    for contribution in contributions:
        evidence.works_exposed += 1
        if contribution.times_completed > 0:
            evidence.works_completed += 1
        if contribution.times_completed > 1:
            evidence.works_reconsumed += 1
        evidence.total_completions += contribution.times_completed
        if contribution.rating is not None:
            evidence.works_rated += 1
            evidence.ratings.append(contribution.rating)
        if contribution.status == STATUS_ABANDONED:
            evidence.works_abandoned += 1
        if contribution.status == STATUS_ON_HOLD:
            evidence.works_on_hold += 1
        if not contribution.in_library:
            evidence.works_removed += 1

    phase1o_finalise(evidence, parameters)
    return evidence


def _status(
    evidence: ConceptEvidence,
    kind: str,
    ambiguous: bool,
    parameters: PreferenceParameters,
    taste: TasteParameters,
) -> str:
    """How much of a claim this pattern's evidence supports.

    Four ways to fall short of established, and none of them invents a
    number:

      At or below the minimum support, a pattern has only just cleared the
      bar that lets it be discussed at all. "Emerging" is what that means, so
      establishment needs strictly more evidence than discovery does.

      An unknown or neutral direction cannot be established however confident
      it is, because there is nothing to be confident about.

      An ambiguous pair rests on evidence that equally supports its
      neighbours, and evidence that cannot tell two claims apart has not
      established either.

      Otherwise the bar is `confidence` at or above the moderate band -- the
      product's existing threshold, reused rather than duplicated, so that
      "established" and what the preference page already calls well-supported
      cannot drift apart.
    """
    minimum = (
        taste.minimum_individual_rated
        if kind == KIND_INDIVIDUAL
        else taste.minimum_combination_rated
    )
    if evidence.works_rated < minimum:
        return STATUS_INSUFFICIENT
    if evidence.works_rated == minimum:
        return STATUS_EMERGING
    if evidence.direction in (DIRECTION_UNKNOWN, DIRECTION_NEUTRAL):
        return STATUS_EMERGING
    if ambiguous:
        return STATUS_EMERGING
    if evidence.confidence >= parameters.confidence_moderate_from:
        return STATUS_ESTABLISHED
    return STATUS_EMERGING


def _pattern(
    features: tuple[Feature, ...],
    contributions: list[WorkContribution],
    kind: str,
    parameters: PreferenceParameters,
    taste: TasteParameters,
    constituent_evidence: tuple[float | None, ...] = (),
    shares_support_with: tuple[str, ...] = (),
) -> TastePattern:
    evidence = _evidence_over(features, contributions, parameters)
    supporting = tuple(
        SupportingWork(
            work_id=item.work_id,
            title=item.title,
            domain_slug=item.domain_slug,
            status=item.status,
            rating=item.rating,
            normalized_rating=item.normalized_rating,
            times_completed=item.times_completed,
            in_library=item.in_library,
        )
        for item in sorted(
            contributions,
            key=lambda item: (item.rating is None, -(item.rating or 0), item.title),
        )
    )
    return TastePattern(
        features=features,
        kind=kind,
        status=_status(
            evidence, kind, bool(shares_support_with), parameters, taste
        ),
        direction=evidence.direction,
        preference_evidence=evidence.preference_evidence,
        confidence=evidence.confidence,
        exposure=evidence.exposure,
        engagement=evidence.engagement,
        reconsumption_signal=evidence.reconsumption_signal,
        abandonment_signal=evidence.abandonment_signal,
        works_rated=evidence.works_rated,
        works_exposed=evidence.works_exposed,
        works_completed=evidence.works_completed,
        works_abandoned=evidence.works_abandoned,
        works_on_hold=evidence.works_on_hold,
        works_reconsumed=evidence.works_reconsumed,
        total_completions=evidence.total_completions,
        ratings_above_baseline=evidence.positive_ratings,
        ratings_below_baseline=evidence.negative_ratings,
        supporting_works=supporting,
        domains=tuple(sorted({item.domain_slug for item in contributions})),
        constituent_evidence=constituent_evidence,
        shares_support_with=shares_support_with,
    )


# --- discovery -------------------------------------------------------------


def _pattern_sort_key(pattern: TastePattern) -> tuple:
    """Deterministic, and the same shape as the Phase 1R page ordering."""
    status_rank = {
        STATUS_ESTABLISHED: 0,
        STATUS_EMERGING: 1,
        STATUS_INSUFFICIENT: 2,
    }[pattern.status]
    kind_rank = 0 if pattern.kind == KIND_INDIVIDUAL else 1
    return (
        status_rank,
        kind_rank,
        -pattern.confidence,
        -abs(pattern.preference_evidence or 0.0),
        pattern.key,
    )


async def build_taste_profile(
    session: AsyncSession,
    user_id: uuid.UUID,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    taste: TasteParameters = DEFAULT_TASTE_PARAMETERS,
) -> TasteProfile:
    """Every taste pattern this user's history supports, and nothing more.

    Reads the Phase 1O profile and regroups it. Nothing is persisted and
    nothing is written; the result is a pure function of interactions,
    ratings and work-concept associations, so two identical histories give
    two identical profiles.
    """
    profile = await build_preference_profile(session, user_id, parameters)

    features: dict[str, Feature] = {}
    # One contribution per work, rebuilt so that no pattern carries a single
    # concept's annotation confidence as though it described the pair.
    per_work: dict[uuid.UUID, WorkContribution] = {}
    work_features: dict[uuid.UUID, set[str]] = defaultdict(set)

    for evidence in profile.concepts:
        if evidence.concept_type not in FEATURE_FAMILIES:
            continue
        features[evidence.concept_slug] = Feature(
            key=evidence.concept_slug,
            name=evidence.concept_name,
            family=evidence.concept_type,
        )
        for contribution in evidence.contributions:
            work_features[contribution.work_id].add(evidence.concept_slug)
            if contribution.work_id not in per_work:
                per_work[contribution.work_id] = WorkContribution(
                    work_id=contribution.work_id,
                    title=contribution.title,
                    domain_slug=contribution.domain_slug,
                    status=contribution.status,
                    rating=contribution.rating,
                    normalized_rating=contribution.normalized_rating,
                    times_completed=contribution.times_completed,
                    in_library=contribution.in_library,
                    concept_confidence=None,
                    concept_method="aggregate",
                )

    diagnostics = TasteDiagnostics(features_available=len(features))
    rejected: dict[str, int] = defaultdict(int)

    # --- individual features, first and always ----------------------------
    works_for: dict[str, list[WorkContribution]] = defaultdict(list)
    for work_id, keys in work_features.items():
        for key in keys:
            works_for[key].append(per_work[work_id])

    rated_works_for: dict[str, frozenset[uuid.UUID]] = {}
    individual: dict[str, TastePattern] = {}
    for key, contributions in works_for.items():
        pattern = _pattern(
            (features[key],), contributions, KIND_INDIVIDUAL, parameters, taste
        )
        individual[key] = pattern
        rated_works_for[key] = frozenset(
            item.work_id for item in contributions if item.rating is not None
        )
        diagnostics.individual_candidates += 1
        if pattern.status == STATUS_INSUFFICIENT:
            diagnostics.individual_below_support += 1

    # --- pairs, only where the evidence earns them ------------------------
    # Candidates come from co-occurrence within the user's own rated works,
    # never from the cross product of the vocabulary.
    pair_works: dict[tuple[str, str], list[WorkContribution]] = defaultdict(list)
    for work_id, keys in work_features.items():
        contribution = per_work[work_id]
        if contribution.rating is None:
            # A pair with no rating behind it has no direction to discover,
            # and unrated exposure must never become a preference.
            continue
        for first, second in itertools.combinations(sorted(keys), 2):
            pair_works[(first, second)].append(contribution)

    diagnostics.pairs_considered = len(pair_works)

    admitted: dict[tuple[str, str], list[WorkContribution]] = {}
    for pair, contributions in pair_works.items():
        first, second = pair
        support = frozenset(item.work_id for item in contributions)

        if len(support) < taste.minimum_combination_rated:
            rejected[REJECTED_MIN_SUPPORT] += 1
            continue
        # A pair whose parts are not themselves patterns is a claim resting
        # on a feature Noema has declined to make a claim about.
        #
        # Unreachable at the default thresholds, where the combination
        # minimum is the larger of the two and a pair's support is a subset
        # of each part's. It is not dead code: raise the individual minimum
        # above the combination one and it fires. Kept so the invariant is
        # enforced rather than assumed to hold for every configuration.
        if (
            individual[first].status == STATUS_INSUFFICIENT
            or individual[second].status == STATUS_INSUFFICIENT
        ):
            rejected[REJECTED_WEAK_PARTS] += 1
            continue
        # Strictly more selective than both parts, or it is a restatement of
        # the simpler claim rather than a more specific one.
        if len(support) >= len(rated_works_for[first]) or len(support) >= len(
            rated_works_for[second]
        ):
            rejected[REJECTED_NOT_SELECTIVE] += 1
            continue

        candidate = _evidence_over((features[first], features[second]), contributions, parameters)
        distinction = taste.combination_distinction(parameters)
        parts = (
            individual[first].preference_evidence,
            individual[second].preference_evidence,
        )
        if candidate.preference_evidence is None or any(
            part is None or abs(candidate.preference_evidence - part) < distinction
            for part in parts
        ):
            rejected[REJECTED_NOT_DISTINCT] += 1
            continue

        admitted[pair] = contributions

    # Pairs resting on the identical set of rated works are one observation,
    # not several. They stay candidates and are flagged; none may become
    # established on evidence that equally supports its neighbours.
    by_support: dict[frozenset[uuid.UUID], list[tuple[str, str]]] = defaultdict(list)
    for pair, contributions in admitted.items():
        by_support[frozenset(item.work_id for item in contributions)].append(pair)
    diagnostics.ambiguous_support_sets = sum(
        1 for pairs in by_support.values() if len(pairs) > 1
    )

    combinations: list[TastePattern] = []
    for pair, contributions in admitted.items():
        siblings = tuple(
            "+".join(other)
            for other in sorted(
                by_support[frozenset(item.work_id for item in contributions)]
            )
            if other != pair
        )
        combinations.append(
            _pattern(
                (features[pair[0]], features[pair[1]]),
                contributions,
                KIND_COMBINATION,
                parameters,
                taste,
                constituent_evidence=(
                    individual[pair[0]].preference_evidence,
                    individual[pair[1]].preference_evidence,
                ),
                shares_support_with=siblings,
            )
        )

    diagnostics.pairs_admitted = len(combinations)
    diagnostics.pairs_rejected = dict(sorted(rejected.items()))

    patterns = [
        pattern
        for pattern in individual.values()
        if pattern.status != STATUS_INSUFFICIENT
    ] + combinations
    patterns.sort(key=_pattern_sort_key)

    return TasteProfile(user_id=user_id, patterns=patterns, diagnostics=diagnostics)


# --- why nothing here is persisted ----------------------------------------
#
# For the same reason Phase 1O is not: every field is a pure function of
# interactions, ratings and work-concept associations, and one changed rating
# invalidates every pattern touching that work. A materialised taste table
# would be a second record of what a user likes, free to drift from the
# authoritative one, in exchange for a speed-up nothing has asked for. The
# whole profile is one query plus a pass over a few hundred pairs.
#
# The moment to revisit is measured, not aesthetic: when the pair scan stops
# fitting in a request, or when profiles are read far more often than
# interactions change. Neither is true at seventeen works.
