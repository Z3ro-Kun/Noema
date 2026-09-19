"""Explicit preference feedback, as a client sends and receives it.

Phase 1X. The request names a canonical concept by its stable slug and gives
a verdict; the response says what is now on record and nothing about the
preference engine, because nothing about the preference engine has changed.

There is no `user_id` field anywhere in this module, in either direction. The
user comes from the session, as it does on every user-scoped route.

---

What the response promises, and what it does not

It reports what was stored: the concept, the verdict, when it was first said
and when it was last said. It does **not** report a new preference, a
recalculated score, or any claim that the profile has moved -- because in
this phase it has not. A client that says "your profile has been corrected"
would be making that up; "Noema will use this" is the honest wording and the
field names here are chosen so the truthful sentence is the easy one.

`corrected` is a disagreement with an interpretation. It is deliberately not
named `dislikes`, `negative` or `wrong_direction`: a user who says "not
really" about *You particularly enjoy fantasy* may be indifferent rather than
opposed, and the contract must not encourage a client to render the stronger
claim.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import FEEDBACK_SURFACES, FEEDBACK_TYPES, SURFACE_TASTE_PROFILE

# Enumerated so a client can switch on them and a test can assert nothing
# else is ever accepted or emitted. Sourced from the model rather than
# retyped, so the API and the check constraint cannot drift.
FEEDBACK_VALUES = FEEDBACK_TYPES
FEEDBACK_SOURCES = FEEDBACK_SURFACES

# The concept's stable slug, which is exactly what `features[].key` on the
# taste dashboard carries -- so a client submits what it was given rather
# than reconstructing an identity from a display string.
ConceptSlugField = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class PreferenceFeedbackCreate(BaseModel):
    """One verdict on one inferred preference."""

    concept_slug: ConceptSlugField = Field(
        description="Canonical concept slug, as `features[].key` on the dashboard."
    )
    feedback: str = Field(
        description="'confirmed' if the reading seems right, 'corrected' if not."
    )
    # Which product surface asked. Defaulted so the taste profile does not
    # have to send it, present so a work page or a recommendation can.
    source: str = Field(default=SURFACE_TASTE_PROFILE)


class PreferenceFeedbackRead(BaseModel):
    """What is on record for one concept."""

    model_config = ConfigDict(from_attributes=True)

    concept_slug: str
    concept_name: str
    feedback: str
    source: str
    # How many times this user has answered about this concept, current
    # answer included. A plain count, not a weight.
    submission_count: int
    first_recorded_at: datetime
    updated_at: datetime


class PreferenceFeedbackEventRead(BaseModel):
    """One answer, kept so changing one's mind adds a fact instead of erasing one."""

    model_config = ConfigDict(from_attributes=True)

    # Null on the first answer: there was nothing to change from.
    feedback_before: str | None
    feedback_after: str
    source: str
    occurred_at: datetime


class PreferenceFeedbackHistory(BaseModel):
    """The current verdict and the answers that produced it, oldest first."""

    concept_slug: str
    concept_name: str
    # Null when this user has never answered about this concept, which is a
    # different state from having answered and been overruled.
    current: PreferenceFeedbackRead | None = None
    events: list[PreferenceFeedbackEventRead] = Field(default_factory=list)


class PreferenceFeedbackList(BaseModel):
    """Everything this user has said, ordered by canonical slug."""

    items: list[PreferenceFeedbackRead] = Field(default_factory=list)


class FeedbackVocabulary(BaseModel):
    """The controlled values, so a client does not hardcode them.

    Each value carries the sentence it is allowed to mean. `corrected` in
    particular is a disagreement and not a negative preference, and saying so
    in the contract is cheaper than hoping every client reads the docs.
    """

    values: list[str] = Field(default_factory=lambda: list(FEEDBACK_VALUES))
    sources: list[str] = Field(default_factory=lambda: list(FEEDBACK_SOURCES))
    meanings: dict[str, str] = Field(
        default_factory=lambda: {
            "confirmed": "The reader agrees this reading of their taste is reasonable.",
            "corrected": (
                "The reader does not agree with this reading. It does not mean "
                "they dislike the concept."
            ),
        }
    )
    # Said plainly, in the contract, so no client can imply otherwise.
    affects_preference_engine: bool = False


__all__ = [
    "FEEDBACK_SOURCES",
    "FEEDBACK_VALUES",
    "FeedbackVocabulary",
    "PreferenceFeedbackCreate",
    "PreferenceFeedbackEventRead",
    "PreferenceFeedbackHistory",
    "PreferenceFeedbackList",
    "PreferenceFeedbackRead",
]
