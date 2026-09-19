"""The taste profile as a reader's client receives it.

Phase 1W. The internal `TasteDashboard` is an inspection surface: it carries
`preference_evidence`, `confidence`, supporting work ids and the alternatives
a finding could not be distinguished from. All of that is useful for checking
the pipeline and none of it belongs in an API response.

This is the product contract instead. It exposes **meaning**, not machinery.

  What crosses          a group, a direction, a confidence *band*, plain
                        counts, the media involved, and a controlled
                        presentation key a renderer turns into a sentence

  What does not         preference evidence, rating signals, normalized
                        ratings, the baseline and spread, shrinkage
                        constants, concept-annotation confidence, extraction
                        methods, source labels, feature families, pattern
                        selection internals, thresholds, and every other
                        number the layers below computed to get here

---

Strength and confidence are different axes

The group -- `strongly_likes`, `mildly_likes`, `dislikes` -- says how much a
reader liked something. `confidence_band` says how much evidence there is for
saying so. They are reported side by side and neither is derived from the
other, so a strong preference with moderate confidence is exactly that: a
clear liking Noema has seen a handful of times. Phase 1V coupled them and got
the first answer wrong in order to hedge the second.

A renderer is expected to use both: the group chooses the verb, the band
chooses the hedge.

---

Nothing here is a claim about the person

Every field describes media preference evidence. There is no trait, no score
about the reader, no recommendation, and no account of *why* anything was
rated -- which Noema does not know and this contract has no field to express.
A fictional theme appearing in works someone rated highly is a fact about what
they enjoy reading and watching, and nothing else.
"""

from pydantic import BaseModel, ConfigDict, Field

# The three established groups, plus early signals. Enumerated so a client can
# switch on them and a test can assert nothing else is ever emitted.
BUCKETS = ("strongly_likes", "mildly_likes", "dislikes", "emerging")

# Direction mirrors Phase 1O exactly rather than reinterpreting it.
DIRECTIONS = ("positive", "negative", "neutral", "unknown")

# Confidence as a word, never a number: 0.54 on screen invites being read as
# "54% certain", which is not what the value means.
CONFIDENCE_BANDS = ("low", "moderate", "high")

# Where a reader's profile is in its life, so a client can choose between an
# onboarding prompt and a profile without inspecting counts itself.
PROFILE_STATES = ("no_activity", "no_ratings", "building", "established")


class FeatureRead(BaseModel):
    """One concept, named for a reader.

    A combination keeps both of its features rather than being flattened into
    one invented name, so a client can render the pair however it likes.
    """

    key: str
    name: str


class EvidenceSummaryRead(BaseModel):
    """What a reader could check the group against, in plain counts."""

    rated_works: int = Field(description="Rated works behind this preference.")
    supporting_works: int = Field(
        description="Works associated with it in total, rated or not."
    )
    domains: list[str] = Field(
        default_factory=list,
        description="Media the supporting works span, by display name.",
    )
    # Behaviour, reported beside the ratings and never folded into them. A
    # work finished three times was finished three times; that is not three
    # ratings, and this flag does not move the preference.
    includes_reconsumed_works: bool = False
    # True when the ratings behind this preference fall on both sides of the
    # reader's own baseline. A client can hedge on it instead of guessing
    # from a confidence number.
    has_mixed_evidence: bool = False


class PreferenceItemRead(BaseModel):
    """One concept or pair, as a reader meets it."""

    key: str
    display_name: str
    features: list[FeatureRead]
    # "individual" or "combination".
    kind: str
    direction: str
    confidence_band: str
    # Controlled key a renderer maps to wording. Never a sentence: the same
    # item must be renderable several ways without the evidence changing.
    presentation_key: str
    domains: list[str] = Field(default_factory=list)
    evidence_summary: EvidenceSummaryRead
    # Findings resting on exactly the same rated works, which the evidence
    # cannot separate. Named so a client can show them rather than imply a
    # choice was made between them.
    also_supported_by: list[str] = Field(default_factory=list)


class StandoutObservationRead(BaseModel):
    """A higher-level observation, still without a sentence attached."""

    observation: str
    presentation_key: str
    features: list[FeatureRead] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    confidence_band: str | None = None
    rated_works: int | None = None


class DashboardSummaryRead(BaseModel):
    """Plain facts about the profile. Deliberately not a score of any kind."""

    profile_state: str
    rated_works: int
    established_preferences: int
    emerging_signals: int


class TasteDashboardResponse(BaseModel):
    """The whole payload for one authenticated reader."""

    model_config = ConfigDict(from_attributes=True)

    summary: DashboardSummaryRead
    strongly_likes: list[PreferenceItemRead] = Field(default_factory=list)
    mildly_likes: list[PreferenceItemRead] = Field(default_factory=list)
    dislikes: list[PreferenceItemRead] = Field(default_factory=list)
    emerging: list[PreferenceItemRead] = Field(default_factory=list)
    what_stands_out: list[StandoutObservationRead] = Field(default_factory=list)


__all__ = [
    "BUCKETS",
    "CONFIDENCE_BANDS",
    "DIRECTIONS",
    "PROFILE_STATES",
    "DashboardSummaryRead",
    "EvidenceSummaryRead",
    "FeatureRead",
    "PreferenceItemRead",
    "StandoutObservationRead",
    "TasteDashboardResponse",
]

# Explicitly not exported, and explicitly not present on any model above:
# `preference_evidence`, `confidence`, `rating_signal`, `normalized_rating`,
# `baseline`, `spread`, `supporting_work_ids`, `concept_confidence`,
# `method`, `source`, `family`, `status`, `works_exposed`. A test enumerates
# the serialized payload and fails if any of them reappears.
_INTERNAL_FIELDS_NEVER_SERIALIZED: tuple[str, ...] = (
    "preference_evidence",
    "confidence",
    "rating_signal",
    "normalized_rating",
    "rating_mean",
    "baseline",
    "spread",
    "shrink",
    "supporting_work_ids",
    "work_id",
    "concept_confidence",
    "method",
    "source",
    "family",
    "salience",
    "specificity",
    "document_frequency",
    "idf",
)
