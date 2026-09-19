"""Projecting Phase 1O's evidence onto the product surface.

A pure mapping. It computes nothing: every value it emits is either copied
from `build_preference_profile` or is a band over a number that function
already produced. The preference mathematics is untouched, and deliberately
so -- changing the engine and the interface in one phase would make any
behavioural regression impossible to attribute.

What it does do is decide what a reader sees, which is a real editorial
choice in four places:

  Splitting the list   Concepts with a rating direction and concepts with
                       only exposure are returned separately, rather than as
                       one list where the second group carries a direction of
                       "unknown". A reader scanning a single list reads
                       everything in it as a preference; separating them is
                       what keeps "you have watched these" from quietly
                       becoming "you like these".

  Banding confidence   A word instead of a number, so 0.54 is never read as
                       "54% certain".

  Ordering signals     Delegated to `ordering.py` since Phase 1R, which
                       measured the two candidates against the evaluation
                       library and adopted confidence-descending. The reasons
                       are recorded there. Phase 1P's band-grouped ordering
                       remains available by name, unchanged, so the
                       comparison stays runnable. Either way this is a
                       sequence, not a computation: no evidence value is
                       recomputed and no ranking is invented.

  Dropping internals   The shrunk baseline, the per-rating normalized values
                       and the content layer's annotation confidence do not
                       cross this boundary.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.preference_product import (
    ContributingWork,
    EvidenceCounts,
    ExposureSignal,
    PreferenceOverview,
    PreferenceSignal,
    PreferenceSummary,
)
from app.services.preference.evidence import (
    DIRECTION_UNKNOWN,
    ConceptEvidence,
    build_preference_profile,
)
from app.services.preference.ordering import (
    DEFAULT_ORDERING,
    confidence_band,
    order_signals,
)
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters

# Re-exported: the band a reader sees and the order they see it in are
# decided together, in `ordering`. Importers of this name are unaffected.
__all__ = ["build_preference_overview", "confidence_band"]

# Domain slugs are internal; a reader sees the domain's display name. Kept
# here rather than looked up per contribution because the set is fixed and
# tiny, and a join per work would be an N+1 for a cosmetic field.
_DOMAIN_NAMES = {
    "literature": "Literature",
    "anime": "Anime",
    "manhwa": "Manga & Manhwa",
}


def _counts(evidence: ConceptEvidence) -> EvidenceCounts:
    return EvidenceCounts(
        works_exposed=evidence.works_exposed,
        works_started=evidence.works_started,
        works_completed=evidence.works_completed,
        works_rated=evidence.works_rated,
        positive_ratings=evidence.positive_ratings,
        negative_ratings=evidence.negative_ratings,
        rating_mean=(
            round(evidence.rating_mean, 1) if evidence.rating_mean is not None else None
        ),
        works_reconsumed=evidence.works_reconsumed,
        total_completions=evidence.total_completions,
        works_abandoned=evidence.works_abandoned,
        works_on_hold=evidence.works_on_hold,
    )


def _contributions(evidence: ConceptEvidence) -> list[ContributingWork]:
    """The works behind a signal, rated ones first and highest first.

    Unrated works still appear -- they are part of why the concept is on the
    page at all -- but after the rated ones, since the rating is what carries
    the direction.
    """
    ordered = sorted(
        evidence.contributions,
        key=lambda item: (item.rating is None, -(item.rating or 0), item.title),
    )
    return [
        ContributingWork(
            work_id=item.work_id,
            title=item.title,
            domain_name=_DOMAIN_NAMES.get(item.domain_slug, item.domain_slug),
            rating=item.rating,
            status=item.status,
            times_completed=item.times_completed,
            in_library=item.in_library,
        )
        for item in ordered
    ]


async def build_preference_overview(
    session: AsyncSession,
    user_id: uuid.UUID,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    max_signals: int = 40,
    max_awaiting: int = 20,
    ordering: str = DEFAULT_ORDERING,
) -> PreferenceOverview:
    """The preference page's whole payload, for one authenticated user.

    `ordering` selects between Phase 1P's band-grouped order and Phase 1R's
    confidence-descending one. It changes the sequence of `signals` and
    nothing else -- same concepts, same directions, same bands, same counts,
    same contributions. The endpoint never passes it; it exists so the
    evaluation harness can build both from one profile.
    """
    profile = await build_preference_profile(session, user_id, parameters)

    directed = []
    awaiting: list[ExposureSignal] = []

    for evidence in profile.concepts:
        if evidence.direction == DIRECTION_UNKNOWN:
            # Exposure without a stated opinion. Its own list, never a
            # direction a reader could mistake for a preference, and never
            # touched by the signal ordering.
            awaiting.append(
                ExposureSignal(
                    concept_slug=evidence.concept_slug,
                    concept_name=evidence.concept_name,
                    concept_type=evidence.concept_type,
                    evidence=_counts(evidence),
                    contributions=_contributions(evidence),
                )
            )
            continue
        directed.append(evidence)

    # Concepts with only exposure are ordered by how much of it there is;
    # the engine ordered them by an evidence value they do not have.
    awaiting.sort(key=lambda item: (-item.evidence.works_completed, item.concept_name))

    # Sequence chosen on the evidence, which still carries the raw confidence;
    # the DTO below deliberately does not.
    signals = [
        PreferenceSignal(
            concept_slug=evidence.concept_slug,
            concept_name=evidence.concept_name,
            concept_type=evidence.concept_type,
            direction=evidence.direction,
            confidence_band=confidence_band(evidence.confidence, parameters),
            evidence=_counts(evidence),
            contributions=_contributions(evidence),
        )
        for evidence in order_signals(directed, ordering, parameters)
    ]

    return PreferenceOverview(
        summary=PreferenceSummary(
            total_interactions=profile.total_interactions,
            works_rated=profile.rating_context.rating_count,
            signals_with_direction=len(signals),
            concepts_awaiting_ratings=len(awaiting),
            rating_context_established=profile.rating_context.is_reliable,
            interactions_without_concepts=profile.interactions_without_concepts,
        ),
        signals=signals[:max_signals],
        awaiting_ratings=awaiting[:max_awaiting],
    )
