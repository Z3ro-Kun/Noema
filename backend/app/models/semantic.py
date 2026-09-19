import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

settings = get_settings()


class Entity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named entity scoped to a work: a character, location, item, etc."""

    __tablename__ = "entities"

    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), index=True)
    # "character" | "location" | "item" | "organization" ...
    entity_type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)


class Concept(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A theme, motif, or trope that can appear across works and domains.

    Unlike Entity, a Concept is not scoped to a single work -- it is the
    unit that makes the shared cross-domain semantic space meaningful.
    """

    __tablename__ = "concepts"

    # The stable identity, mirroring `Domain.slug`. Display names are
    # expected to be reworded as the product matures; matching on `name`
    # would orphan the existing row and silently create a duplicate every
    # time that happened, which is exactly the vocabulary drift the
    # vocabulary module exists to prevent.
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # "theme" | "motif" | "trope"
    concept_type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)


class Embedding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A vector embedding for a content unit, entity, or concept.

    Polymorphic by (owner_type, owner_id) rather than a foreign key, since
    the owner can be any of several tables and this keeps the ML pipeline
    decoupled from any single owning table's schema.

    An embedding is a **computational observation**, never a factual or
    interpretive claim. Nearest-neighbour results are vector similarity and
    must not be written into `Relationship`.

    The reproducibility fields below make it possible to tell whether a
    stored vector still represents its source. A vector is stale when the
    source text, the model, its revision, or the text-preparation version
    has changed since it was written; a stale vector is regenerated rather
    than silently reused.
    """

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "model_name", name="uq_embedding_owner_model"),
    )

    # "content_unit" | "entity" | "concept"
    owner_type: Mapped[str] = mapped_column(String(32), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    model_name: Mapped[str] = mapped_column(String(128))
    vector: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimensions))

    # Which build of the model produced this, where the library reports one.
    model_revision: Mapped[str | None] = mapped_column(String(64))
    # SHA-256 of the *prepared* input text, so a source edit is detectable.
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    # L2-normalized vectors make cosine distance and inner product agree;
    # recorded per row so the metric is never assumed.
    normalized: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped when text preparation changes, which invalidates old vectors
    # even though the text and model are unchanged.
    prep_version: Mapped[int] = mapped_column(Integer, default=1)


class ContentEntity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An occurrence of an entity within a content unit."""

    __tablename__ = "content_entities"

    content_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_units.id"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), index=True
    )
    # How this link was produced: "ner_model", "manual", ...
    method: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)


class ContentConcept(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An occurrence of a concept within a content unit."""

    __tablename__ = "content_concepts"

    content_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_units.id"), index=True
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id"), index=True
    )
    method: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
