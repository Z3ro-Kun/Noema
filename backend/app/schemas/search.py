import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import TEXT_TIERS
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
    container_id: uuid.UUID
    container_type: str
    container_title: str | None
    container_sequence_number: int

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


VALID_TEXT_TIERS = set(TEXT_TIERS)
VALID_REPRESENTATIONS = set(REPRESENTATIONS)
