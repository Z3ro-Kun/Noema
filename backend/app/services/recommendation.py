"""Content-based discovery: which catalogue works this reader has not met.

The first layer in this stack that *suggests* something. Everything before it
describes what a reader's history says; this one points at works they have
not touched and says why.

**It invents no preference mathematics.** The taste dashboard is the only
source of preference here -- the same established patterns a reader can
already see on their own profile page, with the same directions, the same
evidence values and the same confidence bands. Nothing is renormalised,
nothing is re-derived, and there is no second opinion about what a rating
means. If a recommendation says "because you enjoy psychological", the
reader can open their profile and find that exact established pattern.

---

The whole chain, in four steps

    established preferences     from the taste dashboard, unchanged
        -> concepts             the slugs those patterns are about
        -> candidate works      catalogue works carrying those concepts
        -> an explanation       the patterns that matched, by name

That is deliberately all of it. No collaborative filtering -- there is one
user's history in the query and no other user's. No popularity, no rarity, no
inverse document frequency: a concept on eleven works counts exactly as a
concept on one, upholding the rejection Phase 1Q recorded. No learned
ranking, no embedding similarity, no model of the person.

Semantic similarity is deliberately absent too, though the vectors are right
there. Noema already has semantic search; what it did not have was proof that
the concept layer can carry an explainable recommendation on its own. Mixing
the two now would make it impossible to tell which one was doing the work.

---

Scoring, and what it is not

Each matched pattern contributes `|preference evidence| x confidence`. Both
numbers come from the preference engine; multiplying them is the one
arithmetic step this module performs, and it exists so that a strong liking
resting on five agreeing ratings outweighs an equally strong one resting on
two. Positives add, negatives subtract, and a work whose only matches are
negative is never recommended however many positives the reader has.

The score orders candidates and is never shown. It is not a "match
percentage", not a probability and not a taste score: it has no meaning
except relative to the other candidates in the same request, which is why the
API emits reasons and a confidence band instead.

**One contribution per canonical concept.** A reader can have both an
individual pattern for `psychological` and a combination for
`psychological + mystery`; a work carrying both would otherwise be paid twice
for the same concept. Matched patterns are therefore taken strongest first,
and one is accepted only if it brings a concept no accepted pattern has
already claimed. So the count of `work_concepts` rows cannot inflate a score,
and neither can a concept that several patterns happen to mention.

---

What is excluded, and what is not

A work the reader already holds is not a discovery, so every work with an
interaction is excluded -- including ones removed from the library, because
removing something is the clearest statement available that they do not want
it back in front of them. So is a work they have marked *not interested*,
which says the same thing about the recommendation directly.

That suppression is applied at candidate generation and nowhere else. A work
that is not a candidate has no score to lower, so there is no path by which
"do not recommend this to me" could quietly become "dislikes this" -- and it
reaches neither `preference_evidence`, the taste dashboard, rating
normalization, established preferences, semantic search nor library state.

Nothing else excludes a work. Having no embedding does not, having no text
does not, and being metadata-only does not: this layer reads concepts, and a
work AniList catalogued with twelve tags and no summary is exactly as
recommendable as a novel with four thousand paragraphs. That is the point of
building this on concepts rather than on vectors.

---

Honesty when there is nothing to say

A reader with three ratings has not told Noema enough to recommend from, and
the failure mode to avoid is filling the shelf anyway with whatever is
popular. There is no popularity model here to fall back on. The states are
the profile's own -- `no_activity`, `no_ratings`, `building` -- and a client
that gets one of them is expected to show the catalogue instead.
"""

import math
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ACTION_NOT_INTERESTED,
    RECOMMENDATION_FEEDBACK_ACTIONS,
    Concept,
    Domain,
    UserContentInteraction,
    UserRecommendationFeedback,
    Work,
    WorkConcept,
)
from app.services.preference.dashboard import (
    DEFAULT_DASHBOARD_PARAMETERS,
    DashboardParameters,
    PreferenceItem,
    build_taste_dashboard,
)
from app.services.preference.dashboard_product import (
    PROFILE_STATE_BUILDING,
    PROFILE_STATE_ESTABLISHED,
    PROFILE_STATE_NO_ACTIVITY,
    PROFILE_STATE_NO_RATINGS,
    profile_state,
)
from app.services.preference.evidence import DIRECTION_NEGATIVE, DIRECTION_POSITIVE
from app.services.preference.insights import (
    PRESENTATION_ENJOYS_COMBINATION,
    PRESENTATION_ENJOYS_FEATURE,
    PRESENTATION_NEGATIVE_COMBINATION,
    PRESENTATION_NEGATIVE_FEATURE,
)
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters
from app.services.preference.taste import (
    DEFAULT_TASTE_PARAMETERS,
    KIND_COMBINATION,
    TasteParameters,
)

# The three states where a personalized shelf would be a pretence. Reused from
# the profile rather than renamed, so "building" means the same thing on the
# recommendation surface as it does on the taste page.
STATE_NO_ACTIVITY = PROFILE_STATE_NO_ACTIVITY
STATE_NO_RATINGS = PROFILE_STATE_NO_RATINGS
STATE_BUILDING = PROFILE_STATE_BUILDING
# Established preferences exist, and no catalogue work the reader has not
# already met carries them. Distinct from `building`: the shortage is in the
# catalogue, not in the evidence.
STATE_NO_MATCHES = "no_matches"
STATE_PERSONALIZED = "personalized"

STATES = (
    STATE_NO_ACTIVITY,
    STATE_NO_RATINGS,
    STATE_BUILDING,
    STATE_NO_MATCHES,
    STATE_PERSONALIZED,
)

DEFAULT_LIMIT = 12


@dataclass(frozen=True)
class DiversityRule:
    """How much of one shelf one concept or one medium may occupy.

    Not a novelty objective and not a re-ranking system: relevance still
    decides the order, and these only decide when a candidate waits. A reader
    whose evidence is all psychological mystery would otherwise meet four
    psychological mysteries and learn nothing about the catalogue.
    """

    # Two, because one is a finding and three is a theme shelf. A reader with
    # one established preference still sees it lead, twice.
    max_per_leading_concept: int = 2

    # Half a shelf, so no single medium crowds the others out while they have
    # candidates -- and no lower, because a reader whose taste really does sit
    # in one medium should still be served from it.
    def max_per_domain(self, limit: int) -> int:
        return max(2, math.ceil(limit / 2))


DEFAULT_DIVERSITY = DiversityRule()


@dataclass(frozen=True)
class ReasonConcept:
    """One concept behind a reason, named for a reader."""

    key: str
    name: str


@dataclass(frozen=True)
class RecommendationReason:
    """One established pattern that this work matched.

    Everything here came from the dashboard. `presentation_key` is the same
    closed vocabulary the taste page uses, so a renderer that can already
    word "enjoys_feature" needs nothing new.
    """

    presentation_key: str
    direction: str
    concepts: tuple[ReasonConcept, ...]
    confidence_band: str
    # How many of the reader's own rated works stand behind this pattern.
    # A count they can check, not a score.
    rated_works: int

    @property
    def key(self) -> str:
        return "+".join(concept.key for concept in self.concepts)


@dataclass(frozen=True)
class Recommendation:
    """One work to discover, and the evidence that put it there."""

    work_id: uuid.UUID
    title: str
    domain_slug: str
    reasons: tuple[RecommendationReason, ...]
    # Established *negative* patterns this work also matches. Reported rather
    # than hidden: the work is being recommended in spite of them, and a
    # reader is owed that.
    cautions: tuple[RecommendationReason, ...]
    confidence_band: str
    # Internal, all three. They order the candidates, they are what the
    # diagnostics in `recommendation_diagnostics` inspect, and none of them
    # crosses the API boundary -- `RecommendationRead` does not declare them
    # and a test walks the serialized payload to prove it.
    score: float
    support: float = 0.0
    penalty: float = 0.0

    @property
    def leading_concept_key(self) -> str:
        return self.reasons[0].key if self.reasons else ""


@dataclass
class RecommendationResult:
    state: str
    recommendations: list[Recommendation] = field(default_factory=list)
    # Every candidate that scored, in rank order, before the diversity rule
    # cut the shelf. Internal: the endpoint reads `recommendations`, and this
    # exists so diagnostics can see what ranking the scoring produced without
    # re-running the pipeline beside it and risking drift.
    ranked: list[Recommendation] = field(default_factory=list)
    # The concept slugs this reader's established preferences are about.
    # Internal, and the definition of "matched" the diagnostics intersect
    # against -- so there is one such definition rather than two.
    preference_concepts: frozenset[str] = frozenset()
    # Plain counts, for the honest states and for inspection.
    candidates_considered: int = 0
    candidates_matched: int = 0
    established_preferences: int = 0


# --- matching rules, taken from the dashboard unchanged --------------------


@dataclass(frozen=True)
class _Rule:
    """One established pattern, as something a work can match.

    A combination matches only a work carrying *both* its concepts. That is
    what the pattern claims -- aggregation admitted it precisely because it
    was more selective than either part -- so honouring it on one concept
    would be a different claim than the evidence supports.
    """

    concepts: frozenset[str]
    item: PreferenceItem
    contribution: float

    @property
    def direction(self) -> str:
        return self.item.direction

    @property
    def is_positive(self) -> bool:
        return self.item.direction == DIRECTION_POSITIVE


def _presentation_key(item: PreferenceItem) -> str:
    combination = item.kind == KIND_COMBINATION
    if item.direction == DIRECTION_NEGATIVE:
        return (
            PRESENTATION_NEGATIVE_COMBINATION
            if combination
            else PRESENTATION_NEGATIVE_FEATURE
        )
    return PRESENTATION_ENJOYS_COMBINATION if combination else PRESENTATION_ENJOYS_FEATURE


def _reason(item: PreferenceItem) -> RecommendationReason:
    return RecommendationReason(
        presentation_key=_presentation_key(item),
        direction=item.direction,
        concepts=tuple(
            ReasonConcept(key=feature.key, name=feature.name) for feature in item.features
        ),
        confidence_band=item.confidence_band,
        rated_works=item.evidence.works_rated,
    )


def _rules(items: list[PreferenceItem]) -> list[_Rule]:
    """Turn established dashboard items into matchable rules.

    `|evidence| x confidence` is the one arithmetic step this module takes.
    Both factors are the engine's own: strength of the liking, and how much
    the ratings behind it agree and how many there are. An item with no
    evidence value cannot contribute -- there is nothing to weigh.
    """
    rules: list[_Rule] = []
    for item in items:
        evidence = item.evidence.preference_evidence
        if evidence is None or item.direction not in (
            DIRECTION_POSITIVE,
            DIRECTION_NEGATIVE,
        ):
            continue
        rules.append(
            _Rule(
                concepts=frozenset(feature.key for feature in item.features),
                item=item,
                contribution=abs(evidence) * item.evidence.confidence,
            )
        )
    # Strongest first, then by key so two equal contributions resolve the same
    # way on every request.
    rules.sort(key=lambda rule: (-rule.contribution, rule.item.key))
    return rules


# --- scoring one candidate -------------------------------------------------


@dataclass
class _Scored:
    score: float
    support: float
    accepted_positive: list[_Rule]
    accepted_negative: list[_Rule]


def score_candidate(concept_slugs: frozenset[str], rules: list[_Rule]) -> _Scored:
    """What this reader's established preferences say about one work.

    Pure and synchronous, so the rule that matters most here can be tested
    without a database: **one contribution per canonical concept**. Rules are
    taken strongest first and accepted only when they bring a concept no
    accepted rule has claimed, so neither a work's concept count nor a
    concept named by several patterns can inflate the result.
    """
    claimed: set[str] = set()
    positive: list[_Rule] = []
    negative: list[_Rule] = []
    support = 0.0
    penalty = 0.0

    for rule in rules:
        if not rule.concepts <= concept_slugs:
            continue
        if rule.concepts <= claimed:
            # Every concept here is already paid for by a stronger pattern.
            continue
        claimed |= rule.concepts
        if rule.is_positive:
            positive.append(rule)
            support += rule.contribution
        else:
            negative.append(rule)
            penalty += rule.contribution

    return _Scored(
        score=support - penalty,
        support=support,
        accepted_positive=positive,
        accepted_negative=negative,
    )


# --- diversity -------------------------------------------------------------


def apply_diversity(
    ranked: list[Recommendation],
    *,
    limit: int,
    rule: DiversityRule = DEFAULT_DIVERSITY,
) -> list[Recommendation]:
    """Fill the shelf in score order, holding back over-concentration.

    Two passes, and the second is what keeps this a spacing rule rather than a
    filter: anything held back in the first pass is used to fill the shelf if
    relevance alone did not. A reader is never shown fewer works because their
    taste is narrow.
    """
    selected: list[Recommendation] = []
    deferred: list[Recommendation] = []
    per_concept: dict[str, int] = {}
    per_domain: dict[str, int] = {}
    domain_cap = rule.max_per_domain(limit)

    for candidate in ranked:
        if len(selected) >= limit:
            break
        concept = candidate.leading_concept_key
        if (
            per_concept.get(concept, 0) >= rule.max_per_leading_concept
            or per_domain.get(candidate.domain_slug, 0) >= domain_cap
        ):
            deferred.append(candidate)
            continue
        selected.append(candidate)
        per_concept[concept] = per_concept.get(concept, 0) + 1
        per_domain[candidate.domain_slug] = per_domain.get(candidate.domain_slug, 0) + 1

    for candidate in deferred:
        if len(selected) >= limit:
            break
        selected.append(candidate)

    return selected


# --- the query -------------------------------------------------------------


async def _excluded_work_ids(session: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Every work this reader has met, plus every one they have waved away.

    Two different facts, excluded at the same point and for the same reason:
    neither is a discovery.

        an interaction    they already have it, including entries removed from
                          the library -- removing something is the clearest
                          statement the product offers that it should stop
                          being put in front of them.

        not interested    they said so about the recommendation itself.

    The second is a suppression and nothing else. It is applied here, at
    candidate generation, precisely so it cannot reach the scoring: a work
    that is not a candidate has no score to lower, so there is no path by
    which "do not recommend this" could become "dislikes this".
    """
    interacted = (
        await session.execute(
            select(UserContentInteraction.work_id).where(
                UserContentInteraction.user_id == user_id
            )
        )
    ).scalars()
    dismissed = (
        await session.execute(
            select(UserRecommendationFeedback.work_id).where(
                UserRecommendationFeedback.user_id == user_id,
                UserRecommendationFeedback.action == ACTION_NOT_INTERESTED,
            )
        )
    ).scalars()
    return set(interacted) | set(dismissed)


async def build_recommendations(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int = DEFAULT_LIMIT,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    taste: TasteParameters = DEFAULT_TASTE_PARAMETERS,
    dashboard_parameters: DashboardParameters = DEFAULT_DASHBOARD_PARAMETERS,
    diversity: DiversityRule = DEFAULT_DIVERSITY,
) -> RecommendationResult:
    """Works this reader has not met, ranked by their own established taste.

    `user_id` comes from the resolved session at the call site and is the only
    thing that selects whose evidence is read -- the same isolation rule the
    library and preference services follow.
    """
    dashboard = await build_taste_dashboard(
        session, user_id, parameters, taste, dashboard_parameters
    )
    state = profile_state(dashboard)
    if state != PROFILE_STATE_ESTABLISHED:
        return RecommendationResult(state=state)

    rules = _rules(dashboard.established_items())
    wanted = {slug for rule in rules for slug in rule.concepts}
    if not wanted:
        return RecommendationResult(state=STATE_BUILDING)

    excluded = await _excluded_work_ids(session, user_id)

    # One query for the concept side, restricted to the concepts this reader's
    # own preferences are about -- a handful of slugs, not the vocabulary.
    rows = (
        await session.execute(
            select(WorkConcept.work_id, Concept.slug)
            .join(Concept, Concept.id == WorkConcept.concept_id)
            .where(Concept.slug.in_(wanted))
        )
    ).all()

    concepts_by_work: dict[uuid.UUID, set[str]] = {}
    for work_id, slug in rows:
        if work_id in excluded:
            continue
        concepts_by_work.setdefault(work_id, set()).add(slug)

    if not concepts_by_work:
        return RecommendationResult(
            state=STATE_NO_MATCHES,
            established_preferences=len(rules),
        )

    # The works themselves, for their title and domain. Nothing here asks
    # whether they have text, containers or embeddings: a metadata-only work
    # with the right concepts is a real recommendation.
    works = (
        (
            await session.execute(
                select(Work, Domain.slug)
                .join(Domain, Domain.id == Work.domain_id)
                .where(Work.id.in_(list(concepts_by_work)))
            )
        )
        .all()
    )

    scored: list[Recommendation] = []
    for work, domain_slug in works:
        result = score_candidate(frozenset(concepts_by_work[work.id]), rules)
        # Support is required, not merely a positive total: a work whose only
        # established match is something the reader dislikes is not a
        # discovery, whatever the arithmetic says.
        if not result.accepted_positive or result.score <= 0:
            continue
        reasons = tuple(_reason(rule.item) for rule in result.accepted_positive)
        scored.append(
            Recommendation(
                work_id=work.id,
                title=work.title,
                domain_slug=domain_slug,
                reasons=reasons,
                cautions=tuple(_reason(rule.item) for rule in result.accepted_negative),
                # The band of the pattern that led. A reader asking "how sure
                # is this?" is asking about the evidence behind the reason
                # they were given.
                confidence_band=reasons[0].confidence_band,
                score=result.score,
                support=result.support,
                penalty=result.support - result.score,
            )
        )

    # Deterministic to the last key: two identical histories produce two
    # identical shelves, and a tie never resolves by dictionary order.
    scored.sort(key=lambda item: (-item.score, item.title, str(item.work_id)))

    return RecommendationResult(
        state=STATE_PERSONALIZED if scored else STATE_NO_MATCHES,
        recommendations=apply_diversity(scored, limit=limit, rule=diversity),
        ranked=scored,
        preference_concepts=frozenset(wanted),
        candidates_considered=len(concepts_by_work),
        candidates_matched=len(scored),
        established_preferences=len(rules),
    )


__all__ = [
    "DEFAULT_DIVERSITY",
    "DEFAULT_LIMIT",
    "STATES",
    "STATE_BUILDING",
    "STATE_NO_ACTIVITY",
    "STATE_NO_MATCHES",
    "STATE_NO_RATINGS",
    "STATE_PERSONALIZED",
    "DiversityRule",
    "Recommendation",
    "RecommendationReason",
    "RecommendationResult",
    "WorkNotFoundError",
    "apply_diversity",
    "build_recommendations",
    "get_suppression",
    "restore_recommendation",
    "score_candidate",
    "suppress_recommendation",
    "suppressed_work_ids",
]


# --- "not interested" ------------------------------------------------------
#
# A standing instruction about one work's recommendability, and nothing else.
# It lives in this module because suppression is a recommendation concern; it
# writes to its own table and touches no preference state.


class WorkNotFoundError(LookupError):
    """No canonical work with that id."""


async def suppress_recommendation(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    work_id: uuid.UUID,
    action: str = ACTION_NOT_INTERESTED,
) -> UserRecommendationFeedback:
    """Record that this reader does not want this work recommended.

    Idempotent by the table's own uniqueness rather than by a read-modify-
    write: saying it twice is saying it once, and the second call returns the
    row the first one wrote. Nothing is updated on a repeat, so the timestamp
    keeps saying when they actually decided.

    The work is resolved first, so a suppression can never name a work that
    does not exist -- an id that is silently accepted is a filter that quietly
    does nothing.
    """
    if action not in RECOMMENDATION_FEEDBACK_ACTIONS:
        raise ValueError(f"unknown recommendation feedback action {action!r}")

    work = (
        await session.execute(select(Work.id).where(Work.id == work_id))
    ).scalar_one_or_none()
    if work is None:
        raise WorkNotFoundError(str(work_id))

    existing = await get_suppression(session, user_id=user_id, work_id=work_id)
    if existing is not None:
        return existing

    feedback = UserRecommendationFeedback(
        user_id=user_id, work_id=work_id, action=action
    )
    session.add(feedback)
    await session.flush()
    return feedback


async def get_suppression(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID
) -> UserRecommendationFeedback | None:
    """This reader's standing instruction about this work, if there is one.

    Scoped by `user_id` in the query itself, so one reader's row is not
    reachable by another even with the right work id.
    """
    return (
        await session.execute(
            select(UserRecommendationFeedback).where(
                UserRecommendationFeedback.user_id == user_id,
                UserRecommendationFeedback.work_id == work_id,
            )
        )
    ).scalar_one_or_none()


async def restore_recommendation(
    session: AsyncSession, *, user_id: uuid.UUID, work_id: uuid.UUID
) -> bool:
    """Take the instruction back. Returns whether there was one to take back.

    A delete rather than a reversal record: this is a switch, not a verdict,
    and the row's absence is exactly what "recommendable again" means. Also
    idempotent -- undoing twice is undoing once.
    """
    existing = await get_suppression(session, user_id=user_id, work_id=work_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True


async def suppressed_work_ids(
    session: AsyncSession, user_id: uuid.UUID
) -> set[uuid.UUID]:
    """Every work this reader has waved away. Exposed for diagnostics."""
    rows = await session.execute(
        select(UserRecommendationFeedback.work_id).where(
            UserRecommendationFeedback.user_id == user_id,
            UserRecommendationFeedback.action == ACTION_NOT_INTERESTED,
        )
    )
    return set(rows.scalars().all())
