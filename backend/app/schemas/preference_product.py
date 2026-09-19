"""The user-facing view of preference evidence.

Phase 1O's `/preferences` response is an inspection surface: it carries the
shrunk baseline and spread, the raw 0-1 confidence, each rating's normalized
value, and the content layer's own annotation confidence. All of it is
useful for checking the engine and none of it belongs in front of a reader.

This is the product contract instead. Three things are deliberately absent:

  Raw scores        `preference_evidence` and `confidence` do not appear as
                    numbers. A 0.81 on screen invites being read as "81%
                    certain", which is not what either value means.
                    Direction and a confidence *band* carry the same meaning
                    without the false precision.

  Formula internals The shrinkage constants, the baseline, the spread and the
                    per-rating normalized values are implementation. The
                    reader is told that their ratings are read in the context
                    of how they usually rate, and that is the whole of it.

  Annotation detail `WorkConcept.confidence` and `method` describe how sure
                    the *content* layer is that a work carries a concept.
                    Showing it next to a preference invites reading it as
                    confidence in the preference, which Phase 1O went to some
                    length to keep separate.

**Nothing here is a claim about the person.** Every field describes media
preference evidence: what was watched or read, what was rated, and what those
ratings show about concepts. There is no trait, no score about the user, and
no recommendation.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field

# Direction of the evidence. Mirrors Phase 1O exactly rather than
# reinterpreting it: "unknown" means no rating exists, which is a different
# thing from "neutral", where ratings exist and cancel out.
DIRECTIONS = ("positive", "negative", "neutral", "unknown")

# Confidence banded rather than numeric. The thresholds live in the backend
# because the backend defines what the confidence scale means; the words
# shown to a reader are the frontend's business.
CONFIDENCE_BANDS = ("low", "moderate", "high")


class ContributingWork(BaseModel):
    """One work behind a signal, and what this user did with it.

    Identifies the work and the user's own interaction, and nothing else --
    no content units, no text, no ingestion metadata, no concept annotation
    details.
    """

    model_config = ConfigDict(from_attributes=True)

    work_id: uuid.UUID
    title: str
    domain_name: str
    # The rating as the user gave it. Null when they never rated it, which is
    # shown as "not rated" rather than as a zero.
    rating: int | None
    status: str
    times_completed: int
    # False once removed from the library. The evidence survives removal, so
    # the work can still appear here.
    in_library: bool


class EvidenceCounts(BaseModel):
    """What actually happened, in plain counts a reader can check."""

    works_exposed: int
    works_started: int
    works_completed: int
    works_rated: int
    positive_ratings: int
    negative_ratings: int
    rating_mean: float | None = Field(
        description="The plain average of the ratings given, on the 1-10 scale. "
        "Shown as-is; the engine's normalized reading is not exposed."
    )
    # Behavioural context, kept apart from the rating counts above.
    works_reconsumed: int
    total_completions: int
    works_abandoned: int
    works_on_hold: int


class PreferenceSignal(BaseModel):
    """One concept with a direction, supported by explicit ratings."""

    model_config = ConfigDict(from_attributes=True)

    concept_slug: str
    concept_name: str
    concept_type: str

    direction: str = Field(description=" | ".join(DIRECTIONS))
    confidence_band: str = Field(description=" | ".join(CONFIDENCE_BANDS))

    evidence: EvidenceCounts
    contributions: list[ContributingWork]


class ExposureSignal(BaseModel):
    """A concept the user has met but never rated.

    Kept in its own list rather than mixed in with a direction of "unknown",
    so that engagement without a stated opinion reads as what it is. Phase 1O
    deliberately refuses to call an unrated completion weak approval, and
    presenting it separately is how that survives contact with a screen.
    """

    model_config = ConfigDict(from_attributes=True)

    concept_slug: str
    concept_name: str
    concept_type: str
    evidence: EvidenceCounts
    contributions: list[ContributingWork]


class PreferenceSummary(BaseModel):
    """Enough context to know how much the page below rests on."""

    total_interactions: int
    works_rated: int
    signals_with_direction: int
    concepts_awaiting_ratings: int
    # True once the user's own rating history carries most of the weight in
    # normalization. Reported as a flag, never as the underlying number.
    rating_context_established: bool
    # Interactions whose work carries no concepts at all: a gap in Noema's
    # content coverage, not an absence of interest on the user's part.
    interactions_without_concepts: int


class PreferenceOverview(BaseModel):
    """Everything the preference page renders."""

    summary: PreferenceSummary
    # Ordered by the engine, strongest evidence first. The client renders in
    # the order received and adds no ranking meaning of its own.
    signals: list[PreferenceSignal]
    awaiting_ratings: list[ExposureSignal]
