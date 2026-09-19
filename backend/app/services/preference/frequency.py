"""Corpus-level concept frequency, for the Phase 1Q experiment.

**Experiment-only. Retained as research history, imported by nothing in the
product path.** Phase 1Q rejected inverse-frequency weighting and Phase 1R
adopted confidence ordering instead, which reads none of this. The only
importers are `experiment.py`, `scripts/run_idf_experiment.py` and their
tests; if that list ever grows, something has quietly promoted a rejected
formulation.

How common a concept is across canonical works, and nothing to do with any
user. That separation is the point: a concept's weight must be a property of
the corpus, so two users looking at the same concept see the same weight.
Nothing in this module reads an interaction, a rating or a user id, and a
test asserts it.

Frequency is computed from existing `work_concepts` rows. No schema change,
no migration, no materialised table -- at 17 works and 172 associations the
whole thing is one grouped count.
"""

import math
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Concept, Work, WorkConcept


@dataclass(frozen=True)
class FrequencyParameters:
    """How concept frequency becomes a weight.

    `smoothing` is the k in log((N + k) / (df + k)) + 1. Standard idf uses
    k = 1; a larger k damps the rare end, where a difference of one work is
    noise rather than signal. With a 17-work corpus, df = 1 against df = 2 is
    literally one work, and k = 2 keeps that from producing a large swing.

    The whole thing is reported and tested across several k values rather
    than asserted, because the right value is an empirical question and this
    phase is an experiment.
    """

    smoothing: float = 2.0


DEFAULT_FREQUENCY_PARAMETERS = FrequencyParameters()


@dataclass(frozen=True)
class ConceptFrequency:
    """One concept's corpus frequency and the weight derived from it."""

    slug: str
    # Distinct canonical works carrying this concept.
    document_frequency: int
    # log((N + k) / (df + k)) + 1. Larger for rarer concepts.
    idf: float
    # idf rescaled to (0, 1] against the rarest attainable concept, so the
    # weight is bounded and interpretable. A concept on every work keeps a
    # floor share rather than dropping to zero -- being common is not
    # disqualifying, which is the whole caution of this experiment.
    specificity: float


@dataclass(frozen=True)
class CorpusFrequencies:
    """Frequencies for every concept that any work carries."""

    total_works: int
    parameters: FrequencyParameters
    by_slug: dict[str, ConceptFrequency]

    def specificity(self, slug: str) -> float:
        """Weight for a concept, defaulting to the rarest case if unseen.

        An unknown slug means a concept no work carries, which cannot happen
        for a concept reached through an interaction. Returning the maximum
        rather than raising keeps the caller simple; nothing in the corpus
        exercises it.
        """
        entry = self.by_slug.get(slug)
        return entry.specificity if entry else 1.0

    def document_frequency(self, slug: str) -> int:
        entry = self.by_slug.get(slug)
        return entry.document_frequency if entry else 0


def idf(
    document_frequency: int,
    total_works: int,
    parameters: FrequencyParameters = DEFAULT_FREQUENCY_PARAMETERS,
) -> float:
    """Smoothed inverse document frequency.

    log((N + k) / (df + k)) + 1. The +1 keeps a concept on every work at
    weight 1 rather than 0, so the most common concept is damped rather than
    erased.
    """
    smoothing = parameters.smoothing
    return math.log((total_works + smoothing) / (document_frequency + smoothing)) + 1.0


def build_frequencies(
    counts: dict[str, int],
    total_works: int,
    parameters: FrequencyParameters = DEFAULT_FREQUENCY_PARAMETERS,
) -> CorpusFrequencies:
    """Turn raw (slug -> distinct work count) into weights.

    Specificity is idf scaled against the rarest *attainable* concept
    (df = 1), not against an unattainable df = 0. That keeps the top of the
    scale reachable and the bottom above zero: with 17 works and k = 2, a
    concept on every work still carries about 0.35 of the weight of a
    concept on one.
    """
    rarest = idf(1, total_works, parameters) if total_works > 0 else 1.0
    by_slug: dict[str, ConceptFrequency] = {}

    for slug, document_frequency in counts.items():
        value = idf(document_frequency, total_works, parameters)
        by_slug[slug] = ConceptFrequency(
            slug=slug,
            document_frequency=document_frequency,
            idf=round(value, 6),
            specificity=round(min(1.0, value / rarest) if rarest > 0 else 1.0, 6),
        )

    return CorpusFrequencies(
        total_works=total_works, parameters=parameters, by_slug=by_slug
    )


async def load_corpus_frequencies(
    session: AsyncSession,
    parameters: FrequencyParameters = DEFAULT_FREQUENCY_PARAMETERS,
) -> CorpusFrequencies:
    """Concept frequencies across every canonical work.

    Two aggregate queries, both over the canonical side only. No user table
    is touched, so the result is identical for every caller.
    """
    total_works = (
        await session.execute(select(func.count()).select_from(Work))
    ).scalar_one()

    rows = (
        await session.execute(
            select(Concept.slug, func.count(func.distinct(WorkConcept.work_id)))
            .join(WorkConcept, WorkConcept.concept_id == Concept.id)
            .group_by(Concept.slug)
        )
    ).all()

    return build_frequencies({slug: count for slug, count in rows}, total_works, parameters)
