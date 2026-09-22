import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import TEXT_TIERS
from app.schemas.product import WorkPresentation
from app.services.embedding.search import REPRESENTATION_CONTENT_UNIT, REPRESENTATIONS


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=100)
    domain: str | None = Field(default=None, max_length=32)
    text_tier: str | None = Field(default=None)
    work_id: uuid.UUID | None = None
    container_id: uuid.UUID | None = None
    # Which embedded form to search. The two are never blended.
    representation: str = Field(default=REPRESENTATION_CONTENT_UNIT)
    grouping_config: str | None = Field(default=None, max_length=64)


class SearchHitRead(BaseModel):
    """One nearest neighbour.

    `similarity` is cosine similarity between embedding vectors -- a
    computational observation about text, not an assertion that these works
    are related. Present it as similarity, never as a relationship.
    """

    model_config = ConfigDict(from_attributes=True)

    similarity: float
    distance: float
    text_excerpt: str
    text_tier: str
    # "content_unit" (a source unit) or "contextual_passage" (derived from
    # several source units, and not itself something the source contained).
    representation: str
    work_id: uuid.UUID
    work_title: str
    domain_slug: str
    # Null when the hit is a work-level summary -- text describing the work as
    # a whole, for a work the canonical source catalogues no containers for.
    container_id: uuid.UUID | None
    container_type: str | None
    container_title: str | None
    container_sequence_number: int | None

    content_unit_id: uuid.UUID | None = None
    unit_type: str | None = None
    sequence_number: int | None = None
    source_name: str | None = None
    source_url: str | None = None
    licence: str | None = None

    # Traceability for contextual hits: exactly which units produced this.
    passage_id: uuid.UUID | None = None
    source_unit_ids: list[str] | None = None
    unit_count: int | None = None
    first_unit_sequence: int | None = None
    last_unit_sequence: int | None = None
    grouping_config: str | None = None


class SemanticSearchResponse(BaseModel):
    query: str
    model_name: str
    metric: str = "cosine"
    result_kind: str = "semantic_similarity"
    top_k: int
    domain: str | None
    text_tier: str | None
    representation: str
    hits: list[SearchHitRead]


class WorkSearchEvidence(BaseModel):
    """Why one work surfaced, in as little as will answer the question.

    Where the strongest passage sits, how many candidate passages belonged to
    this work, and a short quotation. Deliberately compact: a product search
    result says *that* a work matched and points at the evidence, and a
    surface that printed the passages would be a reading interface for a
    corpus Noema does not redistribute.

    `text_tier` is here for the same reason it is everywhere else. A match
    against a third-party summary is not a match against the work's own
    words, and the difference is not the reader's to guess.
    """

    model_config = ConfigDict(from_attributes=True)

    # Null together when the strongest passage is a work-level summary: it
    # describes the work rather than sitting anywhere inside it. A surface
    # reading these should say so rather than invent a chapter number.
    container_id: uuid.UUID | None
    container_type: str | None
    container_title: str | None
    container_sequence_number: int | None
    text_tier: str
    # how many candidate passages belonged to this work
    matching_passages: int
    excerpt: str
    source_name: str | None
    licence: str | None


class WorkSearchHitRead(WorkPresentation):
    """One work that matched, on the canonical product contract.

    Extends `WorkPresentation` rather than declaring a second work shape, so
    a search result renders with the same component as a Discover card and
    carries the same `user_state` separation -- canonical half shared,
    personal half the caller's own.

    `similarity` is the strongest underlying passage similarity, unchanged
    and unblended. It is a computational observation about text, not a claim
    that the work is about the query.
    """

    similarity: float
    distance: float
    representation: str
    evidence: WorkSearchEvidence


class WorkSearchResponse(BaseModel):
    """`top_k` counts unique works here, not raw passages."""

    query: str
    model_name: str
    metric: str = "cosine"
    result_kind: str = "semantic_similarity"
    top_k: int
    domain: str | None
    text_tier: str | None
    representation: str
    # how many raw passages were examined to produce these works
    candidates_examined: int
    results: list[WorkSearchHitRead]


VALID_TEXT_TIERS = set(TEXT_TIERS)
VALID_REPRESENTATIONS = set(REPRESENTATIONS)
