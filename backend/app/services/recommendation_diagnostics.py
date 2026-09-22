"""Is a richly tagged work advantaged by the recommendation ranking?

A diagnostic, and only a diagnostic. It changes no score, corrects nothing,
and is reachable from no endpoint -- the same standing
`preference/experiment.py` has, and for the same reason: the question needs
evidence before the answer is allowed to touch production behaviour.

---

The concern, stated precisely

Support is a sum over the reader's established preferences that a candidate
matches. A work AniList tagged with eighteen concepts can intersect more of
those preferences than a Gutenberg novel described by four subjects, so it
can accumulate more support -- not because it suits the reader better, but
because the catalogue describes it in more words.

That is not automatically wrong. A work that genuinely carries six themes
somebody likes *is* a better match than one carrying two. The question is
whether the advantage tracks the reader's taste or tracks the tagging, and
those are separable: if concept count predicted rank as well as matched
count does, the ranking would be reading the corpus rather than the reader.

So this module measures both, per candidate:

    total_concepts        how many `work_concepts` rows the work has at all
    matched_concepts      how many of them the reader's preferences name
    accepted              how many contributions actually scored, after the
                          one-per-canonical-concept rule
    support / penalty     the two halves of the score, kept apart
    rank                  where it landed before the diversity rule

and reports the distributions by domain, plus two rank correlations --
concept count against rank, and matched count against rank. Their difference
is the number the future decision rests on.

---

What it deliberately does not do

No IDF, no rarity weighting, no popularity, no normalization by concept
count, no correction of any kind. If the evidence says the effect is real,
that is an argument for an experiment in a later phase, not a licence to
divide by something here.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Concept, Domain, Work, WorkConcept
from app.services.recommendation import (
    STATE_PERSONALIZED,
    RecommendationResult,
    build_recommendations,
)


@dataclass(frozen=True)
class CandidateDiagnostic:
    """One scored candidate, with everything that produced its position."""

    work_id: uuid.UUID
    title: str
    domain_slug: str

    # How many concepts the catalogue attached to this work, in total.
    total_concepts: int
    # How many of those the reader's established preferences are about.
    matched_concepts: int
    # How many contributions actually scored. Lower than `matched_concepts`
    # whenever the one-contribution-per-canonical-concept rule collapsed an
    # overlapping pattern.
    accepted_contributions: int

    positive_total: float
    negative_total: float
    score: float
    # Position in the full ranking, before the diversity rule cut the shelf.
    rank: int


@dataclass
class DomainSummary:
    """Concept counts for one medium, over whatever set was measured."""

    domain_slug: str
    works: int = 0
    concept_counts: list[int] = field(default_factory=list)

    @property
    def mean_concepts(self) -> float:
        return _mean(self.concept_counts)

    @property
    def median_concepts(self) -> float:
        return _median(self.concept_counts)

    @property
    def min_concepts(self) -> int:
        return min(self.concept_counts) if self.concept_counts else 0

    @property
    def max_concepts(self) -> int:
        return max(self.concept_counts) if self.concept_counts else 0


@dataclass
class BiasReport:
    """One reader's ranking, measured against the corpus' tagging."""

    label: str
    state: str
    candidates: list[CandidateDiagnostic] = field(default_factory=list)
    # Rank correlation between a candidate's concept count and its position.
    # Negative means "more concepts, better rank", because rank 1 is best.
    concepts_vs_rank: float | None = None
    matched_vs_rank: float | None = None
    # The same for the score itself, which is easier to read: positive means
    # "more concepts, higher score".
    concepts_vs_score: float | None = None
    matched_vs_score: float | None = None

    def top(self, count: int) -> list[CandidateDiagnostic]:
        return self.candidates[:count]

    def mean_concepts_in_top(self, count: int) -> float:
        return _mean([item.total_concepts for item in self.top(count)])

    def mean_concepts_overall(self) -> float:
        return _mean([item.total_concepts for item in self.candidates])

    def domain_share_in_top(self, count: int) -> dict[str, int]:
        shares: dict[str, int] = defaultdict(int)
        for item in self.top(count):
            shares[item.domain_slug] += 1
        return dict(sorted(shares.items()))


# --- small statistics, written out rather than imported --------------------
#
# scipy is not a dependency and does not become one for four functions. Each
# is the textbook definition; ties are averaged, which is what makes Spearman
# well defined on a corpus where many works share a concept count.


def _mean(values: list[float] | list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list[float] | list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _ranks(values: list[float]) -> list[float]:
    """Competition-free ranks, ties sharing their average position."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def spearman(left: list[float], right: list[float]) -> float | None:
    """Rank correlation. None when there is nothing to correlate.

    Rank rather than Pearson because neither concept count nor score is
    linear in anything: what the question asks is whether *ordering* by one
    predicts ordering by the other.
    """
    if len(left) != len(right) or len(left) < 3:
        return None
    a, b = _ranks(left), _ranks(right)
    mean_a, mean_b = _mean(a), _mean(b)
    covariance = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    spread_a = sum((x - mean_a) ** 2 for x in a) ** 0.5
    spread_b = sum((y - mean_b) ** 2 for y in b) ** 0.5
    if spread_a == 0 or spread_b == 0:
        return None
    return round(covariance / (spread_a * spread_b), 4)


# --- the corpus side -------------------------------------------------------


async def concept_counts_by_work(session: AsyncSession) -> dict[uuid.UUID, int]:
    """How many concepts the catalogue attached to each work."""
    rows = await session.execute(
        select(WorkConcept.work_id, func.count()).group_by(WorkConcept.work_id)
    )
    return {work_id: count for work_id, count in rows.all()}


async def corpus_concept_distribution(
    session: AsyncSession,
) -> dict[str, DomainSummary]:
    """Concept counts per domain over the whole catalogue.

    Works carrying no concepts at all are included with a count of zero --
    leaving them out would flatter every average.
    """
    rows = (
        await session.execute(
            select(Domain.slug, Work.id, func.count(WorkConcept.id))
            .select_from(Work)
            .join(Domain, Domain.id == Work.domain_id)
            .outerjoin(WorkConcept, WorkConcept.work_id == Work.id)
            .group_by(Domain.slug, Work.id)
        )
    ).all()

    summaries: dict[str, DomainSummary] = {}
    for slug, _work_id, count in rows:
        summary = summaries.setdefault(slug, DomainSummary(domain_slug=slug))
        summary.works += 1
        summary.concept_counts.append(int(count))
    return dict(sorted(summaries.items()))


# --- one reader's ranking --------------------------------------------------


async def diagnose(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    label: str = "",
    limit: int = 50,
) -> BiasReport:
    """Measure one reader's full candidate ranking against the corpus tagging.

    Runs the production recommender and reads what it produced. Nothing here
    re-implements scoring: a diagnostic that computed its own numbers would
    be measuring itself.
    """
    result: RecommendationResult = await build_recommendations(
        session, user_id, limit=limit
    )
    report = BiasReport(label=label, state=result.state)
    if result.state != STATE_PERSONALIZED or not result.ranked:
        return report

    totals = await concept_counts_by_work(session)
    carried = await concept_slugs_by_work(
        session, [item.work_id for item in result.ranked]
    )

    for position, item in enumerate(result.ranked, start=1):
        reason_concepts = {
            concept.key for reason in item.reasons for concept in reason.concepts
        }
        caution_concepts = {
            concept.key for caution in item.cautions for concept in caution.concepts
        }
        report.candidates.append(
            CandidateDiagnostic(
                work_id=item.work_id,
                title=item.title,
                domain_slug=item.domain_slug,
                total_concepts=totals.get(item.work_id, 0),
                matched_concepts=len(
                    carried.get(item.work_id, frozenset()) & result.preference_concepts
                ),
                accepted_contributions=len(reason_concepts | caution_concepts),
                positive_total=round(item.support, 6),
                negative_total=round(item.penalty, 6),
                score=round(item.score, 6),
                rank=position,
            )
        )

    concepts = [float(item.total_concepts) for item in report.candidates]
    matches = [float(item.matched_concepts) for item in report.candidates]
    ranks = [float(item.rank) for item in report.candidates]
    scores = [item.score for item in report.candidates]

    report.concepts_vs_rank = spearman(concepts, ranks)
    report.matched_vs_rank = spearman(matches, ranks)
    report.concepts_vs_score = spearman(concepts, scores)
    report.matched_vs_score = spearman(matches, scores)
    return report


async def concept_slugs_by_work(
    session: AsyncSession, work_ids: list[uuid.UUID]
) -> dict[uuid.UUID, frozenset[str]]:
    """Which concepts each of these works carries, by canonical slug."""
    if not work_ids:
        return {}
    rows = (
        await session.execute(
            select(WorkConcept.work_id, Concept.slug)
            .join(Concept, Concept.id == WorkConcept.concept_id)
            .where(WorkConcept.work_id.in_(work_ids))
        )
    ).all()
    gathered: dict[uuid.UUID, set[str]] = defaultdict(set)
    for work_id, slug in rows:
        gathered[work_id].add(slug)
    return {work_id: frozenset(slugs) for work_id, slugs in gathered.items()}


__all__ = [
    "BiasReport",
    "CandidateDiagnostic",
    "DomainSummary",
    "concept_counts_by_work",
    "concept_slugs_by_work",
    "corpus_concept_distribution",
    "diagnose",
    "spearman",
]
