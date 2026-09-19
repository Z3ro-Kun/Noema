"""Phase 1V: the taste dashboard's product contract.

The shape a reader's profile takes: three semantic groups of preferences, a
few higher-level observations, and some plain counts. It is the first layer in
this stack whose job is to be *understood* rather than to be correct about
something new, and it computes no new quantity to do that.

    evidence.py    what a rating means
    taste.py       which features and pairs a history supports
    profile.py     which of those a profile should show
    insights.py    what structural facts hold about the ones it showed
    dashboard.py   how those facts are grouped for a person

---

Three groups, and what decides them

`Strongly likes`, `Mildly likes` and `Dislikes` are **presentation
categories**, not a new score. Every value behind them was produced by Phase
1O and carried unchanged through 1S, 1T and 1U. The rule has two constants,
one of which already existed:

    not_established   anything Phase 1T did not select as an established
                      pattern -- emerging signals, neutral directions, and
                      everything that never cleared aggregation at all
    dislikes          an established pattern whose direction is negative
    strongly_likes    an established positive pattern whose evidence reaches
                      half the scale
    mildly_likes      every other established positive pattern

**Strength and confidence are different things, and only strength decides the
group.** Phase 1V got this wrong: it also required the `high` confidence band
for `strongly_likes`, which meant a reader who rated five works 10, 10, 9, 9,
8 -- about as clear a liking as the scale can express -- was told Noema
*mildly* thought so, because five ratings do not reach that band. The label
describes how much someone liked something. Confidence describes how much
evidence there is for saying so. Conflating them made the first answer wrong
in order to hedge the second.

So the two travel side by side instead. A pattern can be

    strongly_likes + moderate confidence      a clear liking, early days
    strongly_likes + high confidence          a clear liking, well attested
    mildly_likes   + high confidence          a mild liking, well attested

and the item carries its `confidence_band` in every case, so a renderer can
hedge the wording without the classification moving.

*Reliability* still gates entry. To be established at all a pattern must rest
on more than the minimum rated works, agree with itself well enough to reach
the moderate confidence band, and not be one of several findings the evidence
cannot tell apart. So "+0.82 from a single rating" never arrives here; it is
an early signal. That is the right place for reliability to act -- on whether
Noema says anything, not on how warmly it phrases what it says.

Why half the scale: `preference_evidence` is a mean of normalized ratings on
[-1, 1], so 0.5 is the midpoint between "no direction" and "the strongest
reading the scale allows". It is a statement about the scale, not a number
fitted to the evaluation library -- and deliberately not a percentile, a rank
or a share of the current top concepts. A threshold on a bounded absolute
scale means today's classification does not change because an unrelated
concept appeared tomorrow.

The profile is dynamic in the way that matters instead: more supporting
ratings raise confidence while the group holds, and contradictory ratings
move the evidence itself, so an item can travel from strongly to mildly liked
-- or out of the established groups entirely -- as the history changes.

**The negative side is deliberately not symmetrical.** The product defines one
negative group, so `Dislikes` spans what would otherwise be strong and mild
negatives. Each item still carries its own confidence band, so a renderer can
hedge a moderately-supported dislike without a fourth group existing.

---

Equal evidence

Several concepts can rest on the same rated works with the same ratings, the
same evidence and the same confidence. Phase 1T already collapsed those into
one entry carrying the others as alternatives, and this layer does not
re-rank them: they occupy the same group, and `also_supported_by` names the
ones the evidence cannot separate. Nothing here decides that Drama matters
more than Tragedy because it sorts first.

---

What stands out

Only observations that say something a group listing does not. A concept in
`Strongly likes` is already visible; repeating it as "you strongly like
this" would be the same fact twice. What earns a place is a *relationship*:

    a combination the aggregation layer established
    a pattern whose support spans more than one medium
    positive and negative findings coexisting

Cross-domain is an attribute of a bucket item and an observation in its own
right here, which is not a contradiction: in the list it describes a
preference, and in this section the breadth *is* the point. It never moves a
pattern up a group -- a test asserts three domains with one work each does not
outrank one domain with five.

Duplicates are removed by what they *say*, not by a quota. Several concepts
spanning the same pair of media make that point once; a concept spanning a
different set of media makes a different point and stands on its own.

---

What this layer refuses

No taste score. No aggregate scalar about a reader of any kind; the summary
holds counts and nothing else. No corpus frequency, rarity, novelty,
popularity or diversity term. No prose -- every user-visible string is a key
from a closed set, which is the structural form of the promise that a renderer
decides the wording and this module does not. And no claim about why anything
was rated, or about the reader's life.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.evidence import (
    DIRECTION_NEGATIVE,
    DIRECTION_POSITIVE,
    build_preference_profile,
)
from app.services.preference.insights import (
    OBSERVATION_COMBINATION,
    OBSERVATION_OPPOSING,
    OBSERVATION_PATTERN,
    PRESENTATION_ENJOYS_COMBINATION,
    PRESENTATION_ENJOYS_FEATURE,
    PRESENTATION_MIXED_DIRECTIONS,
    PRESENTATION_NEGATIVE_COMBINATION,
    PRESENTATION_NEGATIVE_FEATURE,
    ProfileInsights,
    TasteInsight,
    derive_insights,
)
from app.services.preference.ordering import confidence_band
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters
from app.services.preference.profile import (
    ComposedProfile,
    SelectedPattern,
    compose_from_patterns,
)
from app.services.preference.taste import (
    DEFAULT_TASTE_PARAMETERS,
    KIND_COMBINATION,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    TasteParameters,
    build_taste_profile,
)

# The three groups a reader sees, plus the one that means "not shown".
BUCKET_STRONGLY_LIKES = "strongly_likes"
BUCKET_MILDLY_LIKES = "mildly_likes"
BUCKET_DISLIKES = "dislikes"
BUCKET_NOT_ESTABLISHED = "not_established"
BUCKET_EMERGING = "emerging"

BUCKETS = (
    BUCKET_STRONGLY_LIKES,
    BUCKET_MILDLY_LIKES,
    BUCKET_DISLIKES,
    BUCKET_NOT_ESTABLISHED,
    BUCKET_EMERGING,
)

# Observations that earn their own place in "What stands out". Reused from
# Phase 1U rather than redefined, so the two layers cannot drift.
STANDOUT_COMBINATION = OBSERVATION_COMBINATION
STANDOUT_CROSS_DOMAIN = "cross_domain_pattern"
STANDOUT_OPPOSING = OBSERVATION_OPPOSING

STANDOUTS = (STANDOUT_COMBINATION, STANDOUT_CROSS_DOMAIN, STANDOUT_OPPOSING)

# Controlled keys a renderer maps to wording. The first five come from Phase
# 1U unchanged; the cross-domain pair is added here because breadth is the
# observation rather than an attribute of one.
PRESENTATION_CROSS_DOMAIN_FEATURE = "cross_domain_feature"
PRESENTATION_CROSS_DOMAIN_COMBINATION = "cross_domain_combination"

DASHBOARD_PRESENTATION_KEYS = (
    PRESENTATION_ENJOYS_FEATURE,
    PRESENTATION_NEGATIVE_FEATURE,
    PRESENTATION_ENJOYS_COMBINATION,
    PRESENTATION_NEGATIVE_COMBINATION,
    PRESENTATION_CROSS_DOMAIN_FEATURE,
    PRESENTATION_CROSS_DOMAIN_COMBINATION,
    PRESENTATION_MIXED_DIRECTIONS,
)

# Three to five when the evidence supports it, fewer otherwise, never padded.
MAX_STANDOUTS = 5


@dataclass(frozen=True)
class DashboardParameters:
    """The one constant this layer adds, with its reasoning.

    Separate from `PreferenceParameters` because nothing here changes what a
    rating means; it decides only how a finished value is described.
    """

    # Half of the [-1, 1] scale `preference_evidence` lives on: the midpoint
    # between no direction and the strongest reading the scale allows.
    strong_evidence_from: float = 0.5


DEFAULT_DASHBOARD_PARAMETERS = DashboardParameters()


@dataclass(frozen=True)
class FeatureRef:
    """One concept, named for a reader. Both survive in a combination."""

    key: str
    name: str
    family: str


@dataclass(frozen=True)
class EvidenceSummary:
    """What a reader could check the group against.

    Counts, and the two internal values a future drill-down will want. The
    raw numbers are carried but are explicitly not the presentation: a reader
    is shown a group and a confidence band, never `+0.81`.
    """

    works_rated: int
    works_completed: int
    works_exposed: int
    rating_mean: float | None

    # Behaviour, beside the rating evidence and never folded into it.
    works_reconsumed: int = 0
    total_completions: int = 0
    works_abandoned: int = 0
    works_on_hold: int = 0

    # How many rated works sit above and below this reader's own baseline,
    # carried through from Phase 1O. Together they say whether the ratings
    # behind a preference agree, which a product surface reports as a flag.
    ratings_above_baseline: int = 0
    ratings_below_baseline: int = 0

    # Internal, retained for provenance. A user-facing serializer drops these.
    preference_evidence: float | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class PreferenceItem:
    """One concept or pair, as a reader meets it."""

    key: str
    display_name: str
    features: tuple[FeatureRef, ...]
    kind: str
    direction: str
    bucket: str
    confidence_band: str
    presentation_key: str
    evidence: EvidenceSummary
    domains: tuple[str, ...] = ()

    # The findings this one stands in for, because the evidence cannot tell
    # them apart. Names only -- a reader can be shown them, not ranked by them.
    also_supported_by: tuple[str, ...] = ()

    # Provenance without payload: ids a drill-down can resolve, not embedded
    # work records. See the module docstring.
    supporting_work_ids: tuple[uuid.UUID, ...] = ()

    @property
    def is_combination(self) -> bool:
        return self.kind == KIND_COMBINATION

    @property
    def is_cross_domain(self) -> bool:
        return len(self.domains) > 1


@dataclass(frozen=True)
class StandoutObservation:
    """A higher-level observation, still without a sentence attached."""

    observation: str
    presentation_key: str
    features: tuple[FeatureRef, ...]
    pattern_keys: tuple[str, ...]
    domains: tuple[str, ...] = ()
    confidence_band: str | None = None
    works_rated: int | None = None


@dataclass(frozen=True)
class DashboardSummary:
    """Plain counts. Deliberately not a score of any kind."""

    works_rated: int
    # Everything tracked, rated or not. Carried so a product surface can tell
    # "nothing here yet" from "plenty watched, nothing rated" without
    # reimplementing the distinction.
    total_interactions: int
    concepts_with_established_evidence: int
    strongly_likes: int
    mildly_likes: int
    dislikes: int
    emerging_signals: int
    standouts: int


@dataclass
class TasteDashboard:
    """The product contract: three groups, a few observations, some counts."""

    user_id: uuid.UUID
    strongly_likes: list[PreferenceItem] = field(default_factory=list)
    mildly_likes: list[PreferenceItem] = field(default_factory=list)
    dislikes: list[PreferenceItem] = field(default_factory=list)
    emerging: list[PreferenceItem] = field(default_factory=list)
    what_stands_out: list[StandoutObservation] = field(default_factory=list)
    summary: DashboardSummary | None = None

    def established_items(self) -> list[PreferenceItem]:
        return [*self.strongly_likes, *self.mildly_likes, *self.dislikes]

    def all_items(self) -> list[PreferenceItem]:
        return [*self.established_items(), *self.emerging]

    def item(self, key: str) -> PreferenceItem | None:
        return next((item for item in self.all_items() if item.key == key), None)


# --- classification --------------------------------------------------------


def classify(
    pattern,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    dashboard: DashboardParameters = DEFAULT_DASHBOARD_PARAMETERS,
) -> str:
    """Which group a pattern belongs to. One constant, explained above.

    Reads only `status`, `direction` and `preference_evidence`, all of which
    Phase 1O produced and nothing since has altered. **Confidence is
    deliberately not read here** -- see the module docstring.
    """
    if pattern.status == STATUS_EMERGING:
        return BUCKET_EMERGING
    if pattern.status != STATUS_ESTABLISHED:
        return BUCKET_NOT_ESTABLISHED
    if pattern.preference_evidence is None:
        return BUCKET_NOT_ESTABLISHED
    if pattern.direction == DIRECTION_NEGATIVE:
        return BUCKET_DISLIKES
    if pattern.direction != DIRECTION_POSITIVE:
        # Neutral has no direction to describe. Phase 1S already refuses to
        # establish it, so this is a guard rather than a live path.
        return BUCKET_NOT_ESTABLISHED

    if abs(pattern.preference_evidence) >= dashboard.strong_evidence_from:
        return BUCKET_STRONGLY_LIKES
    return BUCKET_MILDLY_LIKES


def _presentation_key(pattern) -> str:
    combination = pattern.kind == KIND_COMBINATION
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


def _item(
    selected: SelectedPattern,
    parameters: PreferenceParameters,
    dashboard: DashboardParameters,
    names: dict[str, str] | None = None,
) -> PreferenceItem:
    pattern = selected.pattern
    lookup = names or {}
    return PreferenceItem(
        key=pattern.key,
        display_name=" + ".join(feature.name for feature in pattern.features),
        features=tuple(
            FeatureRef(key=f.key, name=f.name, family=f.family)
            for f in pattern.features
        ),
        kind=pattern.kind,
        direction=pattern.direction,
        bucket=classify(pattern, parameters, dashboard),
        confidence_band=confidence_band(pattern.confidence, parameters),
        presentation_key=_presentation_key(pattern),
        evidence=EvidenceSummary(
            works_rated=pattern.works_rated,
            works_completed=pattern.works_completed,
            works_exposed=pattern.works_exposed,
            rating_mean=(
                round(
                    sum(w.rating for w in pattern.supporting_works if w.rating)
                    / sum(1 for w in pattern.supporting_works if w.rating),
                    1,
                )
                if any(w.rating for w in pattern.supporting_works)
                else None
            ),
            works_reconsumed=pattern.works_reconsumed,
            total_completions=pattern.total_completions,
            works_abandoned=pattern.works_abandoned,
            works_on_hold=pattern.works_on_hold,
            ratings_above_baseline=pattern.ratings_above_baseline,
            ratings_below_baseline=pattern.ratings_below_baseline,
            preference_evidence=pattern.preference_evidence,
            confidence=pattern.confidence,
        ),
        domains=pattern.domains,
        # Named as a reader would see them, not as slugs. These are shown,
        # never switched on.
        also_supported_by=tuple(
            lookup.get(key, key) for key in selected.indistinguishable_from
        ),
        supporting_work_ids=tuple(w.work_id for w in pattern.supporting_works),
    )


# --- what stands out -------------------------------------------------------


def _information(observation: StandoutObservation) -> tuple:
    """What an observation actually tells a reader, as a comparable value.

    Each kind of observation carries a different fact, so each has its own
    signature:

      cross-domain   the set of media. The news is "this part of your taste
                     is not confined to one medium", and *which* concept it
                     is about is already visible in its group. Three concepts
                     spanning anime and manga make that point once;
                     a fourth spanning anime, manga *and* literature makes a
                     different point and stands on its own.

      combination    the features. The pair itself is the finding, so two
                     different pairs are two findings.

      opposing       nothing further. It is one fact about the profile.

    Deliberately structural. Nothing is scored or ranked, and which member of
    a duplicate group survives is decided by the order they already arrived
    in -- Phase 1R's, applied upstream.
    """
    if observation.observation == STANDOUT_CROSS_DOMAIN:
        return (observation.observation, observation.domains)
    if observation.observation == STANDOUT_COMBINATION:
        return (
            observation.observation,
            tuple(feature.key for feature in observation.features),
        )
    return (observation.observation,)


def _deduplicate(
    observations: list[StandoutObservation],
) -> list[StandoutObservation]:
    """Drop observations that restate one already present, keeping order.

    Phase 1V capped cross-domain findings at one per profile, which was a
    blunt answer to a real problem: on a reader whose library spans media,
    most patterns are cross-domain, and emitting one observation per pattern
    turned this section into the group listing under a different label. But
    the cap also discarded genuinely different findings -- two concepts
    spanning different sets of media are not the same news.

    Equal information collapses; different information stands.
    """
    seen: set[tuple] = set()
    kept: list[StandoutObservation] = []
    for observation in observations:
        signature = _information(observation)
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(observation)
    return kept


def _ordering_key(item: PreferenceItem) -> tuple:
    """Phase 1R's ordering, reused rather than re-invented."""
    return (
        -item.evidence.confidence,
        -abs(item.evidence.preference_evidence or 0.0),
        item.key,
    )


def _standout(insight: TasteInsight) -> StandoutObservation | None:
    """Only observations a group listing does not already make."""
    evidence = insight.evidence

    if insight.observation == OBSERVATION_OPPOSING:
        return StandoutObservation(
            observation=STANDOUT_OPPOSING,
            presentation_key=PRESENTATION_MIXED_DIRECTIONS,
            features=(),
            pattern_keys=insight.pattern_keys,
        )

    if evidence is None or evidence.status != STATUS_ESTABLISHED:
        return None

    features = tuple(
        FeatureRef(key=f.key, name=f.name, family=f.family) for f in insight.features
    )
    band = confidence_band(evidence.confidence)

    if insight.observation == OBSERVATION_COMBINATION:
        # A relationship the aggregation layer established, which no single
        # concept in a group communicates.
        return StandoutObservation(
            observation=STANDOUT_COMBINATION,
            presentation_key=insight.presentation_key,
            features=features,
            pattern_keys=insight.pattern_keys,
            domains=evidence.domains,
            confidence_band=band,
            works_rated=evidence.works_rated,
        )

    if insight.observation == OBSERVATION_PATTERN and evidence.is_cross_domain:
        return StandoutObservation(
            observation=STANDOUT_CROSS_DOMAIN,
            presentation_key=PRESENTATION_CROSS_DOMAIN_FEATURE,
            features=features,
            pattern_keys=insight.pattern_keys,
            domains=evidence.domains,
            confidence_band=band,
            works_rated=evidence.works_rated,
        )

    return None


# --- composition -----------------------------------------------------------


def build_dashboard(
    profile: ComposedProfile,
    insights: ProfileInsights,
    rating_count: int = 0,
    total_interactions: int = 0,
    names: dict[str, str] | None = None,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    dashboard: DashboardParameters = DEFAULT_DASHBOARD_PARAMETERS,
    max_standouts: int = MAX_STANDOUTS,
) -> TasteDashboard:
    """Group a composed profile for a reader. A pure function of its inputs."""
    grouped: dict[str, list[PreferenceItem]] = {bucket: [] for bucket in BUCKETS}

    # An alternative must be named, not shown as its slug -- and the
    # alternatives are exactly the patterns Phase 1T did *not* select, so the
    # selected ones are the wrong place to look them up. `names` comes from
    # the whole aggregated pool; the selected patterns are a fallback for
    # callers that have nothing else.
    lookup = dict(names or {})
    for item in [*profile.key_patterns, *profile.early_signals]:
        lookup.setdefault(
            item.pattern.key, " + ".join(f.name for f in item.pattern.features)
        )

    for selected in profile.key_patterns:
        item = _item(selected, parameters, dashboard, lookup)
        grouped[item.bucket].append(item)
    for selected in profile.early_signals:
        item = _item(selected, parameters, dashboard, lookup)
        # Emerging never enters an established group, whatever its numbers.
        grouped[BUCKET_EMERGING].append(item)

    standouts = _deduplicate(
        [
            observation
            for observation in (_standout(i) for i in insights.insights)
            if observation is not None
        ]
    )[:max_standouts]

    # Ordered here rather than inherited from the caller. Phase 1T already
    # hands its patterns over in this order, so in practice nothing moves --
    # but a contract that is only deterministic because its input happened to
    # be sorted is not a deterministic contract, and a shuffled-input test
    # says so. The key is Phase 1R's, reused: no new ordering signal, and in
    # particular not domain breadth.
    for bucket, items in grouped.items():
        grouped[bucket] = sorted(items, key=_ordering_key)

    strongly = grouped[BUCKET_STRONGLY_LIKES]
    mildly = grouped[BUCKET_MILDLY_LIKES]
    disliked = grouped[BUCKET_DISLIKES]
    emerging = grouped[BUCKET_EMERGING]

    return TasteDashboard(
        user_id=profile.user_id,
        strongly_likes=strongly,
        mildly_likes=mildly,
        dislikes=disliked,
        emerging=emerging,
        what_stands_out=standouts,
        summary=DashboardSummary(
            works_rated=rating_count,
            total_interactions=total_interactions,
            concepts_with_established_evidence=len(strongly) + len(mildly) + len(disliked),
            strongly_likes=len(strongly),
            mildly_likes=len(mildly),
            dislikes=len(disliked),
            emerging_signals=len(emerging),
            standouts=len(standouts),
        ),
    )


async def build_taste_dashboard(
    session: AsyncSession,
    user_id: uuid.UUID,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    taste: TasteParameters = DEFAULT_TASTE_PARAMETERS,
    dashboard: DashboardParameters = DEFAULT_DASHBOARD_PARAMETERS,
) -> TasteDashboard:
    """One reader's dashboard, from their interactions outward.

    `user_id` is the only thing that selects whose data is read, and it comes
    from the authenticated session at the call site. Nothing in this module
    accepts a user identifier from anywhere else.
    """
    aggregated = await build_taste_profile(session, user_id, parameters, taste)
    profile = compose_from_patterns(user_id, aggregated.patterns)
    insights = derive_insights(profile)
    engine_profile = await build_preference_profile(session, user_id, parameters)
    return build_dashboard(
        profile,
        insights,
        rating_count=engine_profile.rating_context.rating_count,
        total_interactions=engine_profile.total_interactions,
        # Every pattern aggregation found, including the ones selection set
        # aside as indistinguishable -- those are precisely the names a
        # reader needs and the composed profile no longer carries.
        names={
            pattern.key: " + ".join(f.name for f in pattern.features)
            for pattern in aggregated.patterns
        },
        parameters=parameters,
        dashboard=dashboard,
    )


# --- why nothing here is persisted ----------------------------------------
#
# The fourth derived view in a row, and the first that would look like a
# saved description of a person if it were stored. Everything in it is a pure
# function of interactions, ratings and work-concept associations; one changed
# rating invalidates the lot. A "taste profile" table would be a durable
# record of what Noema thinks of someone, free to drift from the evidence
# underneath it, which is precisely the artefact this stack has spent six
# phases avoiding. There is no table, no migration and no cache.
