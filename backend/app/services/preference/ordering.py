"""In what order a reader meets their preference signals.

Phase 1R. **Presentation only.** Nothing here computes, recomputes or
reinterprets a preference: every function takes the `ConceptEvidence` objects
Phase 1O produced and returns the same objects in a different sequence. The
values, the directions and the confidences that arrive are the values, the
directions and the confidences that leave.

It lives in its own module for one reason: the two orderings have to be
runnable side by side against identical evidence, which is what makes the
comparison controlled rather than anecdotal.

---

The two orderings

`order_by_evidence` is Phase 1P's, reproduced exactly. The engine sorts by
evidence value, then confidence, then name; the product layer then groups the
result into confidence bands with a stable sort. So a reader meets the high
band first, and *within* it the largest evidence value leads.

`order_by_confidence` is the candidate. Phase 1Q measured that the baseline's
real weakness is not that broad concepts crowd the page -- they never reached
a top five in any evaluation case -- but that a concept resting on a single
10/10 outranks one resting on five agreeing 9s, because `fmean` treats both
as the same magnitude. Confidence is volume times agreement over the user's
own ratings, so it separates exactly those two.

Ordering by confidence also subsumes the band grouping rather than competing
with it: a band is a monotone function of confidence, so a confidence-sorted
list is already grouped high, then moderate, then low. The Phase 1P grouping
becomes a consequence instead of a second step.

**What confidence is not.** It is not preference strength. A concept can sit
at the top of this list with moderate evidence, because the user rated many
related works consistently. That distinction is the reason the ordering is
kept out of the DTO: the product contract emits a *band*, never a number, and
nothing downstream may present position as magnitude.

---

Tie-breaking

Ties are common here -- confidence is volume x agreement, and two concepts
supported by the same works agree exactly. The chain is:

    1. confidence, descending
    2. |preference_evidence|, descending
    3. concept_name, ascending

The second step is the absolute value deliberately. Signed evidence would
sort every negative signal below every positive one at equal confidence,
which would mean a well-evidenced dislike is systematically buried beneath a
weaker liking -- a presentational claim about which direction matters, made
by a tie-breaker. The magnitude is direction-blind; `direction` still carries
the sign and is never touched.

The third step is `concept_name` rather than the slug, matching the engine's
own final tie-break, so the two orderings resolve identical ties identically
and any difference between them is attributable to the first two keys.

Frequency, specificity, salience, `WorkConcept.confidence` and the number of
contributing concepts are all deliberately absent. Phase 1Q's machinery stays
experimental.
"""

from collections.abc import Iterable

from app.services.preference.evidence import ConceptEvidence
from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters

# Phase 1P's ordering, and the candidate this phase evaluates.
ORDERING_EVIDENCE_FIRST = "evidence-first"
ORDERING_CONFIDENCE_FIRST = "confidence-first"
ORDERINGS = (ORDERING_EVIDENCE_FIRST, ORDERING_CONFIDENCE_FIRST)

# What the product uses. Phase 1R's decision, recorded in one place so the
# comparison harness can ask for either without a flag threaded through the
# API.
DEFAULT_ORDERING = ORDERING_CONFIDENCE_FIRST

# High band first. Used by both orderings, for opposite reasons: the evidence
# ordering needs it as an explicit second pass, the confidence ordering gets
# it for free.
BAND_RANK = {"high": 0, "moderate": 1, "low": 2}


def confidence_band(
    confidence: float, parameters: PreferenceParameters = DEFAULT_PARAMETERS
) -> str:
    """Describe a 0-1 confidence in words. Changes no computation.

    Lives here rather than in `product.py` so that the orderings and the band
    a reader sees cannot drift apart; `product` re-exports it unchanged.
    """
    if confidence >= parameters.confidence_high_from:
        return "high"
    if confidence >= parameters.confidence_moderate_from:
        return "moderate"
    return "low"


def _evidence_value(item: ConceptEvidence) -> float:
    """The engine's own treatment of a missing evidence value."""
    return item.preference_evidence if item.preference_evidence is not None else 0.0


def engine_key(item: ConceptEvidence) -> tuple[float, float, str]:
    """The sort key `build_preference_profile` applies to its own output.

    Restated here so `order_by_evidence` reproduces Phase 1P from the
    evidence itself rather than by depending on the order it was handed.
    """
    return (-_evidence_value(item), -item.confidence, item.concept_name)


def order_by_evidence(
    concepts: Iterable[ConceptEvidence],
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
) -> list[ConceptEvidence]:
    """Phase 1P's ordering: confidence band, then the engine's own order."""
    engine_ordered = sorted(concepts, key=engine_key)
    # Stable, so the engine's order survives inside each band -- which is
    # precisely what Phase 1P did.
    return sorted(
        engine_ordered,
        key=lambda item: BAND_RANK[confidence_band(item.confidence, parameters)],
    )


def order_by_confidence(
    concepts: Iterable[ConceptEvidence],
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
) -> list[ConceptEvidence]:
    """Phase 1R's ordering: how much evidence supports a signal, then size."""
    return sorted(
        concepts,
        key=lambda item: (
            -item.confidence,
            -abs(_evidence_value(item)),
            item.concept_name,
        ),
    )


_ORDERINGS = {
    ORDERING_EVIDENCE_FIRST: order_by_evidence,
    ORDERING_CONFIDENCE_FIRST: order_by_confidence,
}


def order_signals(
    concepts: Iterable[ConceptEvidence],
    ordering: str = DEFAULT_ORDERING,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
) -> list[ConceptEvidence]:
    """Apply one of the named orderings. Unknown names are an error.

    Refusing rather than falling back: an ordering chosen by typo would be an
    invisible change to what every reader sees first.
    """
    try:
        strategy = _ORDERINGS[ordering]
    except KeyError:
        raise ValueError(
            f"unknown ordering {ordering!r}; expected one of {ORDERINGS}"
        ) from None
    return strategy(concepts, parameters)
