"""Phase 1U: structured observations over a composed profile.

The last layer before anything is said out loud, and the one most likely to
be misread as the layer that says it. It does not. It emits labelled facts --
an observation type, the evidence behind it, and a controlled key a renderer
can turn into a sentence -- and every one of them is traceable to a pattern
Phase 1T selected.

    evidence.py   what a rating means
    taste.py      which features and pairs a history supports
    profile.py    which of those a profile should show
    insights.py   what structural facts hold about the ones it showed

The dependency runs one way. This module reads a `ComposedProfile` and writes
nothing back; no layer below it knows it exists.

---

What an insight is, and what it is not

An insight is a *structural fact*: this selected pattern is positive and rests
on five rated works across two media; these two selected patterns point in
opposite directions; this pattern is not yet settled. Each carries the
evidence it was derived from, so a reader of the structure can check it.

An insight is **never** a reason. Noema knows what was consumed and how it
was rated. It does not know whether a work was finished out of curiosity,
obligation, a friend's recommendation, completionism or genuine interest, and
nothing here may imply otherwise. A test asserts that no field on any of these
dataclasses is named for a cause, and that the vocabularies below are closed
sets rather than free text -- which is the structural version of the same
promise, since a fixed enumeration cannot grow a sentence.

An insight is **never** biography. A reader whose highly rated works share
`found-family` has shown positive preference evidence for stories about found
family. That is a fact about media taste. It is not a fact about their
parents, their childhood, their relationships or their mental health, and the
distance between those two statements is the entire reason this layer emits
keys instead of prose.

---

One observation, one insight

Phase 1T's profile already collapsed patterns resting on identical rated
works. What remains would still invite duplication: a selected pattern is
positive, *and* crosses domains, *and* has works the reader returned to. Those
are three attributes of one finding, not three findings, so they are three
fields on a single insight rather than three cards saying the same thing in
different words.

Only observations that are genuinely about something else become their own
insight: `opposing_directions` is about the profile as a whole rather than
about any pattern in it, and an emerging signal is about a pattern the profile
deliberately did not select.

---

Ordering and limits

Ordering reuses what already exists -- established before emerging,
then confidence, then evidence magnitude, then the pattern key. No new
scalar is introduced, and in particular domain breadth is not one: a pattern
seen in three media with one work each is not better evidenced than one seen
in a single medium with five, and the structure keeps `domains` and
`works_rated` separate so a renderer cannot confuse them either.

The caps -- five pattern insights, three emerging -- are ceilings and not
quotas. Five because the profile holds at most eight patterns and a summary
that is as long as what it summarises is not a summary; three for emerging
because a secondary section that rivals the primary one stops being
secondary. Neither number was tuned against the evaluation library, and
nothing is ever added to reach them: a profile supporting one insight gets
one, and a profile supporting none gets none.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.evidence import (
    DIRECTION_NEGATIVE,
    DIRECTION_POSITIVE,
)
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters
from app.services.preference.profile import (
    ComposedProfile,
    SelectedPattern,
    compose_profile,
)
from app.services.preference.taste import (
    DEFAULT_TASTE_PARAMETERS,
    KIND_COMBINATION,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    Feature,
    TasteParameters,
)

# What structural fact was detected. A closed set: an observation type that
# is not one of these cannot be emitted, which is what keeps this layer from
# growing a vocabulary of its own.
OBSERVATION_PATTERN = "pattern_highlight"
OBSERVATION_COMBINATION = "combination_highlight"
OBSERVATION_OPPOSING = "opposing_directions"
OBSERVATION_EMERGING = "emerging_signal"

OBSERVATIONS = (
    OBSERVATION_PATTERN,
    OBSERVATION_COMBINATION,
    OBSERVATION_OPPOSING,
    OBSERVATION_EMERGING,
)

# The controlled key a later renderer turns into wording. Also a closed set,
# and deliberately not a sentence: the same insight must be renderable
# several ways without the evidence underneath it changing.
PRESENTATION_ENJOYS_FEATURE = "enjoys_feature"
PRESENTATION_NEGATIVE_FEATURE = "negative_feature"
PRESENTATION_ENJOYS_COMBINATION = "enjoys_combination"
PRESENTATION_NEGATIVE_COMBINATION = "negative_combination"
PRESENTATION_EMERGING_FEATURE = "emerging_feature"
PRESENTATION_EMERGING_COMBINATION = "emerging_combination"
PRESENTATION_MIXED_DIRECTIONS = "mixed_directions"

PRESENTATION_KEYS = (
    PRESENTATION_ENJOYS_FEATURE,
    PRESENTATION_NEGATIVE_FEATURE,
    PRESENTATION_ENJOYS_COMBINATION,
    PRESENTATION_NEGATIVE_COMBINATION,
    PRESENTATION_EMERGING_FEATURE,
    PRESENTATION_EMERGING_COMBINATION,
    PRESENTATION_MIXED_DIRECTIONS,
)

# Ceilings, not quotas. See the module docstring for why these numbers.
MAX_PATTERN_INSIGHTS = 5
MAX_EMERGING_INSIGHTS = 3


@dataclass(frozen=True)
class InsightWork:
    """One work behind an insight, as a reader could check it.

    Identity, the rating the reader gave, and what they did with it. No
    content units, no passages, no embeddings, no canonical metadata beyond
    the title needed to recognise it.
    """

    work_id: uuid.UUID
    title: str
    domain: str
    rating: int | None
    # The rating read against this reader's own distribution. Present because
    # an insight must be checkable against the evidence it claims; a
    # user-facing DTO is expected to drop it, as Phase 1P's contract does.
    normalized_rating: float | None
    times_completed: int
    in_library: bool


@dataclass(frozen=True)
class InsightEvidence:
    """What was measured. Copied from the pattern, never recomputed."""

    direction: str
    status: str
    confidence: float
    preference_evidence: float | None
    works_rated: int
    supporting_works: tuple[InsightWork, ...]

    # Provenance, not a score. `domains` says where the evidence was seen;
    # `works_rated` says how much of it there is. Three domains with one work
    # each is not five works, and these two fields exist separately so that
    # cannot be blurred.
    domains: tuple[str, ...] = ()

    # Behaviour, reported beside the rating evidence and never multiplied
    # into it. A work finished three times was finished three times; that is
    # not three ratings.
    works_reconsumed: int = 0
    total_completions: int = 0
    works_abandoned: int = 0
    works_on_hold: int = 0
    reconsumption_signal: float = 0.0
    abandonment_signal: float = 0.0

    # For a combination: each constituent's own evidence, so the pair is
    # visibly not an inference from its parts.
    constituent_evidence: tuple[float | None, ...] = ()

    @property
    def domain_count(self) -> int:
        return len(self.domains)

    @property
    def is_cross_domain(self) -> bool:
        return len(self.domains) > 1


@dataclass(frozen=True)
class TasteInsight:
    """One structural fact about a composed profile."""

    observation: str
    presentation_key: str
    # The features involved, kept whole. A combination keeps both of its
    # constituents rather than being flattened into one invented concept.
    features: tuple[Feature, ...]
    # Which selected pattern(s) this was derived from. Always non-empty for
    # pattern-level observations; several for profile-level ones.
    pattern_keys: tuple[str, ...]
    evidence: InsightEvidence | None = None
    # Patterns the profile could not tell apart from this one, carried
    # forward so an interface can say so rather than imply a choice was made.
    indistinguishable_from: tuple[str, ...] = ()

    @property
    def is_combination(self) -> bool:
        return len(self.features) == 2


@dataclass
class InsightDiagnostics:
    """Why the set looks the way it does. Internal only."""

    key_patterns: int = 0
    early_signals: int = 0
    pattern_insights: int = 0
    emerging_insights: int = 0
    profile_insights: int = 0
    pattern_insights_capped: int = 0
    emerging_insights_capped: int = 0


@dataclass
class ProfileInsights:
    """A composed profile's structural observations, and nothing more."""

    user_id: uuid.UUID
    insights: list[TasteInsight] = field(default_factory=list)
    diagnostics: InsightDiagnostics = field(default_factory=InsightDiagnostics)

    def observations(self) -> list[str]:
        return [item.observation for item in self.insights]

    def of_type(self, observation: str) -> list[TasteInsight]:
        return [item for item in self.insights if item.observation == observation]

    def established(self) -> list[TasteInsight]:
        return [
            item
            for item in self.insights
            if item.evidence is not None and item.evidence.status == STATUS_ESTABLISHED
        ]


# --- turning one selected pattern into its evidence ------------------------


def _evidence_of(selected: SelectedPattern) -> InsightEvidence:
    """Copy the pattern's values. Nothing is derived or adjusted here."""
    pattern = selected.pattern
    return InsightEvidence(
        direction=pattern.direction,
        status=pattern.status,
        confidence=pattern.confidence,
        preference_evidence=pattern.preference_evidence,
        works_rated=pattern.works_rated,
        supporting_works=tuple(
            InsightWork(
                work_id=work.work_id,
                title=work.title,
                domain=work.domain_slug,
                rating=work.rating,
                normalized_rating=work.normalized_rating,
                times_completed=work.times_completed,
                in_library=work.in_library,
            )
            for work in pattern.supporting_works
        ),
        domains=pattern.domains,
        works_reconsumed=pattern.works_reconsumed,
        total_completions=pattern.total_completions,
        works_abandoned=pattern.works_abandoned,
        works_on_hold=pattern.works_on_hold,
        reconsumption_signal=pattern.reconsumption_signal,
        abandonment_signal=pattern.abandonment_signal,
        constituent_evidence=pattern.constituent_evidence,
    )


def _presentation_key(selected: SelectedPattern) -> str:
    """Which controlled key describes this pattern.

    `negative_feature` rather than the more obvious `avoids_feature`: the
    reader did not avoid these works, they finished them and rated them
    poorly. A key implying avoidance would describe behaviour the evidence
    contradicts.

    Emerging patterns get their own keys whatever their direction, so a
    renderer cannot phrase an unsettled signal as a settled preference
    without deliberately reading `evidence.direction` as well.
    """
    pattern = selected.pattern
    combination = pattern.kind == KIND_COMBINATION

    if pattern.status == STATUS_EMERGING:
        return (
            PRESENTATION_EMERGING_COMBINATION
            if combination
            else PRESENTATION_EMERGING_FEATURE
        )
    if pattern.direction == DIRECTION_NEGATIVE:
        return (
            PRESENTATION_NEGATIVE_COMBINATION
            if combination
            else PRESENTATION_NEGATIVE_FEATURE
        )
    return (
        PRESENTATION_ENJOYS_COMBINATION
        if combination
        else PRESENTATION_ENJOYS_FEATURE
    )


def _insight_for(selected: SelectedPattern) -> TasteInsight:
    pattern = selected.pattern
    if pattern.status == STATUS_EMERGING:
        observation = OBSERVATION_EMERGING
    elif pattern.kind == KIND_COMBINATION:
        observation = OBSERVATION_COMBINATION
    else:
        observation = OBSERVATION_PATTERN

    return TasteInsight(
        observation=observation,
        presentation_key=_presentation_key(selected),
        features=pattern.features,
        pattern_keys=(pattern.key,),
        evidence=_evidence_of(selected),
        indistinguishable_from=selected.indistinguishable_from,
    )


def _ordering_key(insight: TasteInsight) -> tuple:
    """Established first, then the ordering the rest of the stack uses.

    Deliberately not a function of domain count, and a test says so.
    """
    evidence = insight.evidence
    if evidence is None:
        return (2, 0.0, 0.0, insight.observation)
    return (
        0 if evidence.status == STATUS_ESTABLISHED else 1,
        -evidence.confidence,
        -abs(evidence.preference_evidence or 0.0),
        insight.pattern_keys[0],
    )


def _opposing(insights: list[TasteInsight]) -> TasteInsight | None:
    """Both directions present among the selected patterns.

    Genuinely about the profile rather than about any pattern in it, which is
    why it is its own insight rather than a field on one. It carries no
    evidence of its own: the evidence is the insights it names.
    """
    directions = {
        item.evidence.direction
        for item in insights
        if item.evidence is not None
        and item.evidence.status == STATUS_ESTABLISHED
    }
    if not {DIRECTION_POSITIVE, DIRECTION_NEGATIVE} <= directions:
        return None

    return TasteInsight(
        observation=OBSERVATION_OPPOSING,
        presentation_key=PRESENTATION_MIXED_DIRECTIONS,
        features=(),
        pattern_keys=tuple(
            item.pattern_keys[0]
            for item in insights
            if item.evidence is not None
            and item.evidence.status == STATUS_ESTABLISHED
        ),
    )


def derive_insights(
    profile: ComposedProfile,
    max_pattern_insights: int = MAX_PATTERN_INSIGHTS,
    max_emerging_insights: int = MAX_EMERGING_INSIGHTS,
) -> ProfileInsights:
    """The derivation itself: a pure function of a composed profile.

    Separated from the query so it can be exercised on constructed profiles,
    and so that determinism is testable by feeding the same patterns in a
    different order.
    """
    pattern_insights = [_insight_for(item) for item in profile.key_patterns]
    pattern_insights.sort(key=_ordering_key)
    capped_patterns = pattern_insights[:max_pattern_insights]

    emerging_insights = [_insight_for(item) for item in profile.early_signals]
    emerging_insights.sort(key=_ordering_key)
    capped_emerging = emerging_insights[:max_emerging_insights]

    insights = [*capped_patterns]
    # Derived from what is actually shown, not from everything discovered: an
    # observation about a profile must be true of the profile a reader sees.
    contrast = _opposing(capped_patterns)
    if contrast is not None:
        insights.append(contrast)
    insights.extend(capped_emerging)

    return ProfileInsights(
        user_id=profile.user_id,
        insights=insights,
        diagnostics=InsightDiagnostics(
            key_patterns=len(profile.key_patterns),
            early_signals=len(profile.early_signals),
            pattern_insights=len(capped_patterns),
            emerging_insights=len(capped_emerging),
            profile_insights=1 if contrast is not None else 0,
            pattern_insights_capped=len(pattern_insights) - len(capped_patterns),
            emerging_insights_capped=len(emerging_insights) - len(capped_emerging),
        ),
    )


async def build_profile_insights(
    session: AsyncSession,
    user_id: uuid.UUID,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    taste: TasteParameters = DEFAULT_TASTE_PARAMETERS,
) -> ProfileInsights:
    """Structured observations for one user, from the interactions outward."""
    profile = await compose_profile(session, user_id, parameters, taste)
    return derive_insights(profile)


# --- why nothing here is persisted ----------------------------------------
#
# A view over a view over a view. Aggregation is a pure function of
# interactions and work-concept associations, selection a pure function of
# aggregation, and this a pure function of selection; one changed rating
# invalidates all three. Storing insights would create a fourth record of
# what a reader likes, free to drift from the three beneath it, and would be
# the first thing in this stack that looked like a saved profile of a person.
# There is no table, no migration and no cache.
