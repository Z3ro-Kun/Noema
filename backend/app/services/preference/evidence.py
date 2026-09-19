"""Preference evidence: what a user's history says about each concept.

**A derived view, not a stored entity.** Every value here is a deterministic
function of `user_content_interactions` and `work_concepts`, both of which
remain authoritative. Nothing is written, nothing is cached, and there is no
migration -- see the module note at the bottom for why.

The one principle this layer exists to enforce:

    consumption is not preference

so the channels are kept apart rather than summed into a number:

    exposure       met this concept at all
    engagement     actually consumed it
    rating         said explicitly what they thought of it
    reconsumption  went back to it
    abandonment    stopped -- ambiguous, and kept ambiguous

`preference_evidence` is driven by the **rating channel alone**. Completing
something unrated moves `engagement`, never `preference_evidence`, and a
concept with no ratings reports direction "unknown" rather than a weak
positive. Finishing a book is evidence of engagement; it is not a statement
about whether the reader liked it, and this layer declines to turn one into
the other.

Direction and confidence are separate fields on purpose. One 10/10 gives
direction "positive" with low confidence, which is the honest reading; a
single number would have to pretend otherwise.

Every signal carries its own attribution: which works produced it, and which
ratings. Nothing in the output cannot be traced back to specific rows.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import fmean, pstdev

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_ON_HOLD,
    Concept,
    Domain,
    UserContentInteraction,
    Work,
    WorkConcept,
)
from app.services.preference.normalization import (
    RatingContext,
    build_rating_context,
    normalize_rating,
    saturate,
)
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters

DIRECTION_POSITIVE = "positive"
DIRECTION_NEGATIVE = "negative"
DIRECTION_NEUTRAL = "neutral"
# No explicit rating exists for this concept, so no direction can be read.
# Distinct from "neutral", which means ratings exist and average out.
DIRECTION_UNKNOWN = "unknown"


@dataclass
class WorkContribution:
    """One work's contribution to one concept's evidence.

    References canonical rows by id and carries only what is needed to
    explain the signal. No canonical metadata is copied beyond the title
    needed to make the explanation readable.
    """

    work_id: uuid.UUID
    title: str
    domain_slug: str
    status: str
    rating: int | None
    # The rating read against this user's own distribution, or None if unrated.
    normalized_rating: float | None
    times_completed: int
    in_library: bool
    # What the *content* layer said about this work carrying this concept.
    # Reported for inspection and deliberately NOT folded into the user's
    # preference confidence -- see `ConceptEvidence.confidence`.
    concept_confidence: float | None
    concept_method: str


@dataclass
class ConceptEvidence:
    """Everything this user's history says about one concept."""

    concept_slug: str
    concept_name: str
    concept_type: str

    # --- raw, observable counts ------------------------------------------
    # Facts about what happened. No interpretation.
    works_exposed: int = 0
    works_started: int = 0
    works_completed: int = 0
    works_rated: int = 0
    works_abandoned: int = 0
    works_on_hold: int = 0
    works_removed: int = 0
    works_reconsumed: int = 0
    total_completions: int = 0
    ratings: list[int] = field(default_factory=list)

    # --- derived signals --------------------------------------------------
    # Interpretation. Each stays in its own channel.
    exposure: float = 0.0  # 0..1
    engagement: float = 0.0  # 0..1
    rating_signal: float | None = None  # -1..1, None when unrated
    reconsumption_signal: float = 0.0  # 0..1
    abandonment_signal: float = 0.0  # 0..1, ambiguous by construction

    # --- the summary, with its direction and confidence kept apart --------
    preference_evidence: float | None = None  # -1..1, None when unrated
    direction: str = DIRECTION_UNKNOWN
    confidence: float = 0.0  # 0..1

    contributions: list[WorkContribution] = field(default_factory=list)

    @property
    def positive_ratings(self) -> int:
        return sum(
            1
            for contribution in self.contributions
            if contribution.normalized_rating is not None
            and contribution.normalized_rating > 0
        )

    @property
    def negative_ratings(self) -> int:
        return sum(
            1
            for contribution in self.contributions
            if contribution.normalized_rating is not None
            and contribution.normalized_rating < 0
        )

    @property
    def rating_mean(self) -> float | None:
        return fmean(self.ratings) if self.ratings else None


@dataclass
class PreferenceProfile:
    """One user's evidence across every concept they have any history with.

    Named for what it is -- aggregated media-preference evidence. It carries
    no traits, no personality claims, and no conclusions about the person.
    """

    user_id: uuid.UUID
    rating_context: RatingContext
    concepts: list[ConceptEvidence] = field(default_factory=list)
    # Interactions that reached no concept at all, e.g. a work whose source
    # supplied no mappable labels. Reported so a missing concept is visible
    # as a coverage gap rather than looking like an absence of interest.
    interactions_without_concepts: int = 0
    total_interactions: int = 0


async def _load_rows(session: AsyncSession, user_id: uuid.UUID):
    """Every interaction this user has, with the concepts of each work.

    One query. Uses an outer join so a work carrying no concepts still
    appears -- that is a coverage gap worth reporting, not a row to drop.
    Removed interactions are included: a work someone completed, rated 9 and
    then tidied away is still the strongest evidence they gave.
    """
    statement = (
        select(
            UserContentInteraction,
            Work.id,
            Work.title,
            Domain.slug,
            Concept.slug,
            Concept.name,
            Concept.concept_type,
            WorkConcept.confidence,
            WorkConcept.method,
        )
        .join(Work, UserContentInteraction.work_id == Work.id)
        .join(Domain, Work.domain_id == Domain.id)
        .outerjoin(WorkConcept, WorkConcept.work_id == Work.id)
        .outerjoin(Concept, WorkConcept.concept_id == Concept.id)
        .where(UserContentInteraction.user_id == user_id)
        .order_by(Work.title, Concept.name)
    )
    return (await session.execute(statement)).all()


def _direction(
    evidence: float | None, parameters: PreferenceParameters
) -> str:
    if evidence is None:
        return DIRECTION_UNKNOWN
    if evidence > parameters.neutral_band:
        return DIRECTION_POSITIVE
    if evidence < -parameters.neutral_band:
        return DIRECTION_NEGATIVE
    return DIRECTION_NEUTRAL


def _confidence(
    normalized: list[float], parameters: PreferenceParameters
) -> float:
    """How much to trust the *direction*, from rated works only.

    Two factors, multiplied:

      volume       n / (n + k). One rating is weak evidence however extreme
                   it is, which is why direction and confidence are separate
                   fields rather than one number.
      agreement    1 - spread/2, floored at zero. Five ratings that disagree
                   wildly support a direction less than five that concur.
                   Normalized ratings span -1..1, so a spread of 1.0 (half
                   the range) drives agreement to zero.

    Deliberately ignores exposure, engagement and reconsumption: those say
    how much the user *did*, not how confident we are about what they
    thought. Folding them in is how consumption leaks into preference.
    """
    count = len(normalized)
    if count == 0:
        return 0.0
    volume = saturate(count, parameters.confidence_half_point)
    agreement = 1.0
    if count > 1:
        agreement = max(0.0, 1.0 - pstdev(normalized))
    return round(volume * agreement, 6)


async def build_preference_profile(
    session: AsyncSession,
    user_id: uuid.UUID,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
) -> PreferenceProfile:
    """Aggregate one user's interaction history into per-concept evidence.

    Deterministic: the same rows always produce the same profile.
    """
    rows = await _load_rows(session, user_id)

    # The rating context is built from the user's whole history, not from the
    # works carrying any one concept. A per-concept baseline would measure a
    # concept against itself and could never find a preference.
    interactions: dict[uuid.UUID, UserContentInteraction] = {}
    for interaction, *_ in rows:
        interactions[interaction.id] = interaction
    all_ratings = [
        interaction.rating
        for interaction in interactions.values()
        if interaction.rating is not None
    ]
    context = build_rating_context(all_ratings, parameters)

    grouped: dict[str, ConceptEvidence] = {}
    concept_free_interactions: set[uuid.UUID] = set()

    for (
        interaction,
        work_id,
        title,
        domain_slug,
        concept_slug,
        concept_name,
        concept_type,
        concept_confidence,
        concept_method,
    ) in rows:
        if concept_slug is None:
            concept_free_interactions.add(interaction.id)
            continue

        evidence = grouped.get(concept_slug)
        if evidence is None:
            evidence = ConceptEvidence(
                concept_slug=concept_slug,
                concept_name=concept_name,
                concept_type=concept_type,
            )
            grouped[concept_slug] = evidence

        normalized = (
            normalize_rating(interaction.rating, context, parameters)
            if interaction.rating is not None
            else None
        )

        # Exactly one contribution per (work, concept). A work carrying
        # eighteen concepts contributes one work's worth of evidence to each
        # of them -- never eighteen times as much to any one.
        evidence.contributions.append(
            WorkContribution(
                work_id=work_id,
                title=title,
                domain_slug=domain_slug,
                status=interaction.status,
                rating=interaction.rating,
                normalized_rating=normalized,
                times_completed=interaction.times_completed,
                in_library=interaction.removed_at is None,
                concept_confidence=concept_confidence,
                concept_method=concept_method,
            )
        )

        evidence.works_exposed += 1
        if interaction.started_at is not None:
            evidence.works_started += 1
        if interaction.times_completed > 0:
            evidence.works_completed += 1
        if interaction.times_completed > 1:
            evidence.works_reconsumed += 1
        evidence.total_completions += interaction.times_completed
        if interaction.rating is not None:
            evidence.works_rated += 1
            evidence.ratings.append(interaction.rating)
        if interaction.status == STATUS_ABANDONED:
            evidence.works_abandoned += 1
        if interaction.status == STATUS_ON_HOLD:
            evidence.works_on_hold += 1
        if interaction.removed_at is not None:
            evidence.works_removed += 1

    for evidence in grouped.values():
        _finalise(evidence, parameters)

    profile = PreferenceProfile(
        user_id=user_id,
        rating_context=context,
        concepts=sorted(
            grouped.values(),
            key=lambda item: (
                -(item.preference_evidence if item.preference_evidence is not None else 0.0),
                -item.confidence,
                item.concept_name,
            ),
        ),
        interactions_without_concepts=len(concept_free_interactions),
        total_interactions=len(interactions),
    )
    return profile


def _finalise(evidence: ConceptEvidence, parameters: PreferenceParameters) -> None:
    """Turn one concept's counts into its derived signals."""
    evidence.exposure = round(
        saturate(evidence.works_exposed, parameters.exposure_half_point), 6
    )
    evidence.engagement = round(
        saturate(evidence.works_completed, parameters.engagement_half_point), 6
    )
    # Repeat completions only: the first time through is engagement, not
    # reconsumption.
    repeats = max(0, evidence.total_completions - evidence.works_completed)
    evidence.reconsumption_signal = round(
        saturate(repeats, parameters.reconsumption_half_point), 6
    )
    evidence.abandonment_signal = round(
        saturate(evidence.works_abandoned, parameters.abandonment_half_point), 6
    )

    normalized = [
        contribution.normalized_rating
        for contribution in evidence.contributions
        if contribution.normalized_rating is not None
    ]
    if normalized:
        evidence.rating_signal = round(fmean(normalized), 6)
        # The summary is the rating channel and nothing else. Reconsumption
        # and engagement are reported beside it, never blended into it: a
        # user who re-read something they rated 4 has engaged more, not liked
        # it more.
        evidence.preference_evidence = evidence.rating_signal
    else:
        evidence.rating_signal = None
        evidence.preference_evidence = None

    evidence.direction = _direction(evidence.preference_evidence, parameters)
    evidence.confidence = _confidence(normalized, parameters)


async def concept_evidence_for(
    session: AsyncSession,
    user_id: uuid.UUID,
    concept_slug: str,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
) -> ConceptEvidence | None:
    """One concept's evidence, for a focused explanation."""
    profile = await build_preference_profile(session, user_id, parameters)
    for evidence in profile.concepts:
        if evidence.concept_slug == concept_slug:
            return evidence
    return None


# --- why nothing here is persisted ----------------------------------------
#
# Preference evidence is a derived analytical view, not a durable product
# entity. It has no independent lifecycle: every field is a pure function of
# interactions and work-concept associations, and changing a single rating
# invalidates the evidence for every concept of that work.
#
# Persisting it would buy nothing at the current size -- the whole profile is
# one indexed query over a handful of rows -- while creating a second place
# where "what this user likes" is recorded, free to drift from the
# authoritative one. That is a real correctness risk in exchange for a
# performance gain nothing has asked for.
#
# The moment to revisit is a measured one: when profiles are read far more
# often than interactions change, or when the aggregate stops fitting in a
# request. Neither is true, so there is no table and no migration.
