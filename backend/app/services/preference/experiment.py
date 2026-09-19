"""Phase 1Q: the inverse-concept-frequency experiment.

**Experiment-only, and rejected. Nothing in the product path calls this.**
Retained as research history. The Phase 1O engine in `evidence.py` is
untouched and remains the only thing `/preferences` and
`/preferences/overview` read; since Phase 1R the order those signals arrive
in is decided by `ordering.py`, which reads nothing from here. This module wraps that engine's output, adds
frequency-derived fields beside it, and changes none of them.

---

The question this phase was asked

    Do ubiquitous concepts receive too much aggregate preference evidence
    simply because they occur on many works?

The premise assumes evidence accumulates with the number of contributing
works. Phase 1O does not aggregate that way. It takes the **mean** of the
normalized ratings of the works carrying a concept:

    preference_evidence(c) = fmean(normalized_rating(w) for w carrying c)

A mean does not grow with the number of terms. A user who rates five works
9/10, all carrying "Tragedy", gives Tragedy a mean of about 0.83 -- the same
figure one such work would have produced. Measured across the evaluation
cases, the correlation between document frequency and evidence magnitude is
+0.20, -0.26 and +0.12: inconsistent, and it changes sign.

What *does* rise with frequency is the number of supporting rated works
(correlations +0.45, +0.57, +0.82), and therefore confidence, and therefore
position in a page that groups by confidence band. The dominance Phase 1P
observed is real; its mechanism is ordering, not magnitude.

Two consequences follow, and they shape everything below.

**Weighting the evidence magnitude would be wrong.** idf(c) is constant
across the works carrying c, so

    fmean(x_i * idf) == fmean(x_i) * idf

exactly. There is no aggregation boundary at which per-work weighting
behaves differently from scaling the concept afterwards. And scaling breaks
the contract: with this corpus every concept's `evidence * idf` leaves the
[-1, 1] range that `direction`, the neutral band and the whole product layer
depend on. Redemption reaches +2.66. The naive variant is computed here
anyway, as `naive_weighted_evidence`, so the report can show that rather
than assert it.

**Frequency belongs in salience, not in direction.** What a rare concept
earns is a better claim on the reader's attention, not a stronger opinion.
So the experimental output leaves `preference_evidence`, `direction` and
`confidence` exactly as the baseline produced them, and adds:

    specificity   corpus-level weight from document frequency, in (0, 1]
    salience      |evidence| * confidence * specificity, in [0, 1]

Salience is an ordering signal and nothing else. Confidence is deliberately
untouched: how rare a concept is says nothing about how sure we are what the
*user* thought of it, and inflating confidence with rarity would be exactly
the kind of leak Phase 1O was built to prevent.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.evidence import ConceptEvidence, build_preference_profile
from app.services.preference.frequency import (
    DEFAULT_FREQUENCY_PARAMETERS,
    CorpusFrequencies,
    FrequencyParameters,
    load_corpus_frequencies,
)
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters


@dataclass
class WeightedConceptEvidence:
    """One concept's baseline evidence, plus frequency-derived fields.

    The baseline fields are copied verbatim. If any of them ever differs from
    what `evidence.py` produced, this module has a bug.
    """

    concept_slug: str
    concept_name: str

    # --- baseline, copied unchanged from Phase 1O ------------------------
    preference_evidence: float | None
    direction: str
    confidence: float
    works_rated: int

    # --- corpus frequency, user-independent -------------------------------
    document_frequency: int
    idf: float
    specificity: float

    # --- the experimental signal -----------------------------------------
    # |evidence| * confidence * specificity. An ordering signal: it says how
    # much a concept deserves attention, never how much the user liked it.
    # Zero when there is no rating direction at all.
    salience: float = 0.0

    # --- the variant this phase rejects, kept for the record -------------
    # evidence * idf. Reported so the argument against it rests on measured
    # values rather than on assertion; it routinely leaves [-1, 1].
    naive_weighted_evidence: float | None = None

    @property
    def naive_out_of_range(self) -> bool:
        return (
            self.naive_weighted_evidence is not None
            and abs(self.naive_weighted_evidence) > 1.0
        )


@dataclass
class WeightedProfile:
    """A user's concepts under the experimental formulation."""

    user_id: uuid.UUID
    frequencies: CorpusFrequencies
    concepts: list[WeightedConceptEvidence] = field(default_factory=list)

    def by_slug(self) -> dict[str, WeightedConceptEvidence]:
        return {item.concept_slug: item for item in self.concepts}

    def ranked_by_salience(self) -> list[WeightedConceptEvidence]:
        return sorted(
            self.concepts, key=lambda item: (-item.salience, item.concept_slug)
        )

    def ranked_by_baseline(self) -> list[WeightedConceptEvidence]:
        """The baseline's own ordering: evidence, then confidence, then name."""
        return sorted(
            self.concepts,
            key=lambda item: (
                -(item.preference_evidence if item.preference_evidence is not None else 0.0),
                -item.confidence,
                item.concept_name,
            ),
        )


def weight_concept(
    evidence: ConceptEvidence, frequencies: CorpusFrequencies
) -> WeightedConceptEvidence:
    """Attach frequency fields to one concept's baseline evidence."""
    entry = frequencies.by_slug.get(evidence.concept_slug)
    document_frequency = entry.document_frequency if entry else 0
    concept_idf = entry.idf if entry else 1.0
    specificity = entry.specificity if entry else 1.0

    salience = 0.0
    naive = None
    if evidence.preference_evidence is not None:
        salience = round(
            abs(evidence.preference_evidence) * evidence.confidence * specificity, 6
        )
        naive = round(evidence.preference_evidence * concept_idf, 6)

    return WeightedConceptEvidence(
        concept_slug=evidence.concept_slug,
        concept_name=evidence.concept_name,
        # Copied, never recomputed.
        preference_evidence=evidence.preference_evidence,
        direction=evidence.direction,
        confidence=evidence.confidence,
        works_rated=evidence.works_rated,
        document_frequency=document_frequency,
        idf=concept_idf,
        specificity=specificity,
        salience=salience,
        naive_weighted_evidence=naive,
    )


async def build_weighted_profile(
    session: AsyncSession,
    user_id: uuid.UUID,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    frequency_parameters: FrequencyParameters = DEFAULT_FREQUENCY_PARAMETERS,
) -> WeightedProfile:
    """The experimental view of one user's evidence.

    Calls the production engine and decorates its output. Every baseline
    value passes through untouched, which is what makes the comparison a
    controlled one.
    """
    baseline = await build_preference_profile(session, user_id, parameters)
    frequencies = await load_corpus_frequencies(session, frequency_parameters)

    return WeightedProfile(
        user_id=user_id,
        frequencies=frequencies,
        concepts=[weight_concept(item, frequencies) for item in baseline.concepts],
    )
