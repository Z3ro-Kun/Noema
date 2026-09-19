"""Response shapes for preference evidence.

Deliberately worded as *evidence*, never as a claim about the person. There
is no trait here, no label, and no sentence of the form "you are X" -- this
layer reports what a user's own ratings and behaviour show about media
concepts, and stops.

The split that matters is visible in the field names: raw counts describe
what happened, derived signals describe how this layer reads it, and
`contributions` lets a reader check the reading against the rows it came
from.
"""

import uuid
from datetime import datetime  # noqa: F401  (kept for future timestamped fields)

from pydantic import BaseModel, ConfigDict, Field


class ContributionRead(BaseModel):
    """One work's contribution to one concept's evidence."""

    model_config = ConfigDict(from_attributes=True)

    work_id: uuid.UUID
    title: str
    domain_slug: str
    status: str
    rating: int | None
    # The same rating read against this user's own distribution.
    normalized_rating: float | None
    times_completed: int
    in_library: bool
    # What the content layer said about this work carrying this concept.
    # Shown for inspection; it is NOT part of the user's confidence.
    concept_confidence: float | None
    concept_method: str


class ConceptEvidenceRead(BaseModel):
    """Everything a user's history says about one concept."""

    model_config = ConfigDict(from_attributes=True)

    concept_slug: str
    concept_name: str
    concept_type: str

    # --- raw counts: what happened, with no interpretation ---------------
    works_exposed: int
    works_started: int
    works_completed: int
    works_rated: int
    works_abandoned: int
    works_on_hold: int
    works_removed: int
    works_reconsumed: int
    total_completions: int
    ratings: list[int]
    positive_ratings: int
    negative_ratings: int
    rating_mean: float | None

    # --- derived signals: this layer's reading, one channel each ---------
    exposure: float = Field(description="0-1. Met this concept. Not preference.")
    engagement: float = Field(description="0-1. Actually consumed it. Not preference.")
    rating_signal: float | None = Field(
        description="-1..1 from explicit ratings only. Null when unrated."
    )
    reconsumption_signal: float = Field(
        description="0-1 from repeat completions. A separate channel from rating."
    )
    abandonment_signal: float = Field(
        description="0-1. Ambiguous by construction; never negative preference."
    )

    # --- the summary, direction and confidence kept apart ----------------
    preference_evidence: float | None = Field(
        description="-1..1, driven by ratings alone. Null when there are none."
    )
    direction: str = Field(
        description="positive | negative | neutral | unknown. "
        "'unknown' means no rating exists; 'neutral' means ratings average out."
    )
    confidence: float = Field(
        description="0-1 in the direction, from how many ratings agree. "
        "Independent of how much the user consumed."
    )

    contributions: list[ContributionRead]


class RatingContextRead(BaseModel):
    """How this user uses the rating scale, and how well we know it."""

    model_config = ConfigDict(from_attributes=True)

    rating_count: int
    observed_mean: float | None
    observed_spread: float | None
    # After shrinkage toward the scale prior -- what normalization used.
    baseline: float
    spread: float
    normalization_confidence: float = Field(
        description="0-1. How much of the correction came from this user's own "
        "ratings rather than the prior. Low means the raw scale dominates."
    )


class PreferenceProfileRead(BaseModel):
    """One user's concept evidence. Media preference only; no traits."""

    model_config = ConfigDict(from_attributes=True)

    rating_context: RatingContextRead
    total_interactions: int
    # Interactions whose work carries no concepts -- a content coverage gap,
    # reported so it does not look like an absence of interest.
    interactions_without_concepts: int
    concepts: list[ConceptEvidenceRead]
