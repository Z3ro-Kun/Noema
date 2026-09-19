"""Interpreting a rating relative to the user who gave it.

A raw 7/10 does not mean the same thing from two people. Phase 1N's
evaluation case H rates its favourites 6 and 7 and everything else 2 and 3;
case A rates its favourites 9 and 10. Comparing those numbers directly reads
H as lukewarm about the things they like most.

Correcting for that naively -- subtracting each user's own mean -- breaks the
opposite way. Case A's mean is 9.0, so their 8/10 would become *negative*
evidence. A user who rates generously still likes the thing they gave 8.

So a rating is read as a blend of two things:

    absolute   where it sits on the scale the product offered.
               (r - midpoint) / half_range, giving 1 -> -1, 5.5 -> 0, 10 -> +1.

    relative   where it sits in this user's own use of that scale.
               (r - personal_baseline) / personal_spread, clamped.

    normalized = (1 - w) * absolute + w * relative

Two things keep the blend honest:

  Shrinkage    The personal baseline and spread are shrunk toward priors in
               proportion to how few ratings the user has given. With three
               ratings, "this user's average" is barely an estimate at all,
               and shrinkage says so instead of pretending otherwise. This is
               ordinary empirical-Bayes borrowing of strength, the same
               correction used for small-sample averages generally.

  A capped w   The relative term never exceeds half the blend, so a 9/10 can
               never come out negative and a 2/10 can never come out
               positive. Relative reading *adjusts* the absolute one; it does
               not overrule it.

`normalization_confidence` is reported alongside, so a caller can see how
much of the correction was the user's own data rather than the prior. With
no rating history it is 0 and the result is purely the absolute reading --
which is the honest answer, not a failure.

Nothing here is fitted to the evaluation cases. The parameters are stated in
`parameters.py` with their reasoning, and the tests pass their own values to
show that no behaviour depends on the defaults.
"""

import statistics
from dataclasses import dataclass

from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters


@dataclass(frozen=True)
class RatingContext:
    """How one user uses the rating scale.

    Built once per user and reused for every rating, so the same distribution
    underlies every concept's evidence.
    """

    rating_count: int
    observed_mean: float | None
    observed_spread: float | None
    # After shrinkage toward the priors -- these are what normalization uses.
    baseline: float
    spread: float
    # 0..1: how much of the correction comes from this user's own ratings
    # rather than the prior. Reported, never silently folded into anything.
    normalization_confidence: float
    relative_weight: float

    @property
    def is_reliable(self) -> bool:
        """True once the user's own distribution carries most of the weight."""
        return self.normalization_confidence >= 0.5


def build_rating_context(
    ratings: list[int], parameters: PreferenceParameters = DEFAULT_PARAMETERS
) -> RatingContext:
    """Summarise how a user uses the scale, from every rating they have given.

    `ratings` is the user's *whole* rating history, not the ratings for one
    concept. A personal baseline computed from only the works carrying one
    concept would be circular: it would measure that concept against itself.
    """
    count = len(ratings)
    midpoint = parameters.scale_midpoint()

    observed_mean = statistics.fmean(ratings) if count else None
    # Population standard deviation: this is the whole of what the user has
    # rated, not a sample drawn from it.
    observed_spread = statistics.pstdev(ratings) if count > 1 else None

    baseline = (
        (count * observed_mean + parameters.baseline_prior_weight * midpoint)
        / (count + parameters.baseline_prior_weight)
        if observed_mean is not None
        else midpoint
    )

    spread_observed = observed_spread if observed_spread is not None else parameters.spread_prior
    spread = (
        count * spread_observed + parameters.spread_prior_weight * parameters.spread_prior
    ) / (count + parameters.spread_prior_weight)
    spread = max(spread, parameters.minimum_spread)

    confidence = (
        count / (count + parameters.baseline_prior_weight)
        if count
        else 0.0
    )
    relative_weight = parameters.max_relative_weight * (
        count / (count + parameters.relative_weight_prior)
    )

    return RatingContext(
        rating_count=count,
        observed_mean=observed_mean,
        observed_spread=observed_spread,
        baseline=baseline,
        spread=spread,
        normalization_confidence=confidence,
        relative_weight=relative_weight,
    )


def absolute_component(
    rating: int, parameters: PreferenceParameters = DEFAULT_PARAMETERS
) -> float:
    """Where a rating sits on the offered scale, in -1..1."""
    return (rating - parameters.scale_midpoint()) / parameters.scale_half_range()


def relative_component(
    rating: int, context: RatingContext, parameters: PreferenceParameters = DEFAULT_PARAMETERS
) -> float:
    """Where a rating sits in this user's own use of the scale, clamped to -1..1.

    Clamped rather than unbounded: beyond roughly one shrunk spread from the
    baseline, further distance says little, and leaving it unbounded would
    let one outlier dominate a concept's whole signal.
    """
    return _clamp((rating - context.baseline) / context.spread, -1.0, 1.0)


def normalize_rating(
    rating: int, context: RatingContext, parameters: PreferenceParameters = DEFAULT_PARAMETERS
) -> float:
    """A rating as preference evidence in -1..1, given how this user rates.

    -1 is the strongest negative evidence a single rating can carry, +1 the
    strongest positive, 0 neutral.
    """
    weight = context.relative_weight
    blended = (1.0 - weight) * absolute_component(rating, parameters) + weight * (
        relative_component(rating, context, parameters)
    )
    return _clamp(blended, -1.0, 1.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def saturate(count: float, half_point: float) -> float:
    """Convert a count to 0..1 with diminishing returns.

    n / (n + k): zero at zero, 0.5 at n = k, approaching but never reaching 1.
    Chosen over a hard cap because there is no defensible count at which a
    signal should stop growing entirely, and over a linear scale because the
    difference between one and two works matters far more than between
    eleven and twelve.
    """
    if count <= 0:
        return 0.0
    return count / (count + half_point)
