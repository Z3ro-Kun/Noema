"""The recommendation shelf's public contract.

A product payload, not a ranking dump. What crosses this boundary is the
work, the established preferences that matched it, and a word for how much
evidence stands behind the leading one.

What deliberately does not cross it: the candidate score, the preference
evidence value, the confidence number, `WorkConcept.confidence`, the
normalization internals, and every constant in the scoring rule. A reader is
owed "because you enjoy psychological mystery, from four works you rated" --
which is checkable against their own profile page -- and is not owed a
coefficient they would have to trust.

`presentation_key` is the taste page's own closed vocabulary rather than a
sentence, for the same reason it is there: the wording is the client's, and a
backend that shipped prose would be making the claim itself.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.product import WorkPresentation


class ReasonConceptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str


class RecommendationReasonRead(BaseModel):
    """One established preference this work matched.

    `direction` is `positive` for a reason and `negative` for a caution, so a
    client cannot render a dislike as an endorsement by reading the wrong
    list.
    """

    model_config = ConfigDict(from_attributes=True)

    presentation_key: str
    direction: str
    concepts: list[ReasonConceptRead]
    confidence_band: str
    # The reader's own rated works behind this preference. A count they can
    # check on their profile, never a score.
    rated_works: int


class RecommendationRead(WorkPresentation):
    """One work to discover, on the canonical product contract.

    Extends `WorkPresentation` rather than declaring a second work shape, so
    a recommendation renders with the same card as a Discover result and
    carries the same `user_state` separation.
    """

    reasons: list[RecommendationReasonRead]
    # Established negatives this work also matches. It is being recommended in
    # spite of them, and saying so is more honest than filtering silently.
    cautions: list[RecommendationReasonRead] = []
    confidence_band: str


class RecommendationSummaryRead(BaseModel):
    """Where this reader is, and how much the shelf rests on.

    `state` is the profile's own vocabulary: `no_activity`, `no_ratings` and
    `building` mean exactly what they mean on the taste page, and a client
    that receives one should show the catalogue rather than an empty shelf.
    `no_matches` is different -- the evidence exists and the catalogue has
    nothing new carrying it.
    """

    state: str
    established_preferences: int
    # Catalogue works the reader has not already met that carry any concept
    # their established preferences are about.
    candidates_considered: int
    candidates_matched: int


class RecommendationResponse(BaseModel):
    summary: RecommendationSummaryRead
    recommendations: list[RecommendationRead]


class RecommendationFeedbackCreate(BaseModel):
    """What a reader says about a recommendation.

    One admitted action. `not_interested` means "do not recommend this work
    to me" and nothing else -- it is not a rating, not a dislike of the work,
    not a dislike of its concepts and not a statement that the reasoning was
    wrong.
    """

    action: Literal["not_interested"] = "not_interested"


class RecommendationFeedbackRead(BaseModel):
    """The standing instruction, as a client receives it."""

    model_config = ConfigDict(from_attributes=True)

    work_id: uuid.UUID
    action: str
    created_at: datetime
    # Says what the row does, so a client is not left to infer the effect
    # from the action name.
    suppressed_from_recommendations: bool = True
