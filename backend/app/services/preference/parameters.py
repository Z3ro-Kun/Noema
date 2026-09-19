"""Tunable constants for the preference evidence layer, in one place.

Every number the engine uses lives here with the reasoning behind it, rather
than being scattered through the code as literals. They are a frozen
dataclass rather than environment configuration because they are modelling
decisions, not deployment ones: changing them changes what the evidence
*means*, so a change belongs in review, not in a `.env`.

Tests pass their own instance to demonstrate that no behaviour depends on a
particular value.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PreferenceParameters:
    """Parameters governing normalization, saturation and confidence."""

    # --- the rating scale itself -----------------------------------------
    rating_min: int = 1
    rating_max: int = 10

    # --- personal-baseline shrinkage --------------------------------------
    # A user's own mean is a poor baseline when they have rated three things.
    # Both baseline and spread are shrunk toward a prior, empirical-Bayes
    # style: estimate = (n * observed + k * prior) / (n + k). With n = 0 the
    # prior is used outright; as n grows the user's own behaviour takes over.
    #
    # `baseline_prior_weight` is deliberately the same order as a small
    # library (5 ratings), so that a handful of ratings moves the baseline
    # only halfway. Without this shrinkage a user who rates everything 9 or
    # 10 gets a baseline of 9.5, which would make their 9s read as *negative*
    # -- a well-known failure of naive mean-centring.
    baseline_prior_weight: float = 5.0
    spread_prior: float = 2.0
    spread_prior_weight: float = 5.0
    # Floor on the shrunk spread. A user whose ratings are all identical has
    # an observed spread of zero; dividing by it would turn a 0.1-point
    # difference into an infinite signal.
    minimum_spread: float = 0.5

    # --- blending absolute against relative -------------------------------
    # A rating carries two kinds of information: where it sits on the scale
    # the product offered (absolute), and where it sits in this user's own
    # use of that scale (relative). Neither alone is right, so the two are
    # blended.
    #
    # The relative weight is capped strictly below 0.5 so the absolute
    # reading always carries at least half the signal. A 9/10 must never
    # become negative evidence merely because the user is generous, and a
    # 3/10 must never become positive merely because the user is harsh.
    max_relative_weight: float = 0.5
    relative_weight_prior: float = 5.0

    # --- saturation -------------------------------------------------------
    # Counts are converted to 0..1 with n / (n + k): diminishing returns, no
    # ceiling to pick, and a clear half-way point at n = k. Exposure
    # saturates fastest because meeting a concept three times already says
    # most of what count alone can say.
    exposure_half_point: float = 3.0
    engagement_half_point: float = 3.0
    reconsumption_half_point: float = 2.0
    abandonment_half_point: float = 2.0

    # --- confidence -------------------------------------------------------
    # Confidence in the *direction* of a preference, driven by how many rated
    # works support it and how much they agree. One 10/10 should not produce
    # a confident conclusion.
    confidence_half_point: float = 3.0

    # Evidence inside this band of zero is reported as "neutral" rather than
    # as a weak direction. Without it, a single 6/10 would be filed as a
    # positive preference, which is more than the evidence supports.
    neutral_band: float = 0.1

    # --- presenting confidence --------------------------------------------
    # Banding thresholds for the product surface. These change no
    # computation: `confidence` is produced exactly as Phase 1O produced it,
    # and these only decide which word describes it. They live here because
    # the backend defines what the confidence scale means -- a client that
    # picked its own cut-offs would be inventing semantics.
    #
    # Calibrated against the scale's own shape rather than against the
    # evaluation set: confidence is volume x agreement, where volume is
    # n / (n + 3). A single rating therefore cannot exceed 0.25 however
    # extreme it is, three agreeing ratings reach 0.5, and twenty reach 0.87.
    confidence_moderate_from: float = 0.35
    confidence_high_from: float = 0.60

    def scale_midpoint(self) -> float:
        """The neutral point of the offered scale: 5.5 on a 1-10 scale."""
        return (self.rating_min + self.rating_max) / 2.0

    def scale_half_range(self) -> float:
        """Distance from the midpoint to either end: 4.5 on a 1-10 scale."""
        return (self.rating_max - self.rating_min) / 2.0


DEFAULT_PARAMETERS = PreferenceParameters()
