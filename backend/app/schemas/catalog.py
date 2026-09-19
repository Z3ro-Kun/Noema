import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DomainRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None


class WorkSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    original_title: str | None
    domain_slug: str
    source: str | None
    created_at: datetime


class WorkDetail(WorkSummary):
    description: str | None
    # Provenance: what the source told us and where it came from. Kept
    # distinct from anything the ML pipeline will later compute.
    external_ids: dict | None
    extra_metadata: dict | None


class ContainerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    container_type: str
    sequence_number: int
    title: str | None
    extra_metadata: dict | None
    content_unit_count: int


class TextSourceRead(BaseModel):
    """Where a piece of text came from and under what terms.

    Reflects what the source declared at retrieval time; it is not a legal
    determination, and redistributing derived data may need separate review.
    """

    model_config = ConfigDict(from_attributes=True)

    source_name: str
    source_ref: str
    source_url: str | None
    revision_ref: str | None
    retrieved_at: datetime
    licence: str
    licence_url: str | None
    attribution_text: str | None
    requires_attribution: bool
    share_alike: bool


class ContentUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_type: str
    sequence_number: int
    text_content: str | None
    # "primary" = the work's own words; "summary" = a third party describing
    # it. Clients must not present the two as the same thing.
    text_tier: str
    text_source: TextSourceRead | None
    extra_metadata: dict | None


class EntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    entity_type: str
    description: str | None
    extra_metadata: dict | None


class RelationshipRead(BaseModel):
    """A relationship as stored, including how it was produced.

    `source` is the field that keeps a fact an external source asserted
    ("source") distinguishable from anything the pipeline computes
    ("computed"). Clients must not present the two identically.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    predicate: str
    object_type: str
    object_id: uuid.UUID
    object_title: str | None
    source: str
    method: str | None
    score: float | None
    confidence: float | None


class WorkConceptRead(BaseModel):
    """A concept associated with a work, with its attribution.

    User-facing: name, type and where the association came from. The raw
    supporting labels stay internal -- they are ingestion provenance for
    debugging, not something a reader needs.
    """

    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    concept_type: str
    description: str | None
    # "source" = a catalogue stated this | "computed" = our pipeline derived
    # it. Present so a characterization is never shown as an unattributed fact.
    source: str
    method: str
    # The source's own stated relevance rescaled to 0-1, or null when the
    # source stated none. Not a probability and not comparable across methods.
    confidence: float | None
