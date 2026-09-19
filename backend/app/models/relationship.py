import uuid

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Relationship(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A directed edge between two entities/concepts/works.

    Subject and object are polymorphic (type + id) rather than foreign keys,
    since either side can be an Entity, Concept, or Work. `source`
    distinguishes a fact an external source told us ("source") from
    something our own pipeline computed ("computed"); `method`, `score`,
    and `confidence` record how a computed relationship was produced so it
    can be presented as an observation rather than settled fact.
    """

    __tablename__ = "relationships"

    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    predicate: Mapped[str] = mapped_column(String(128), index=True)
    object_type: Mapped[str] = mapped_column(String(32), index=True)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)

    # "source" (an external source told us this) | "computed" (our pipeline
    # inferred it) | "user" (a user asserted it)
    source: Mapped[str] = mapped_column(String(32))
    # e.g. "embedding_cosine_similarity", "co_occurrence", "manual"
    method: Mapped[str | None] = mapped_column(String(128))
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)

    evidence: Mapped[list["Evidence"]] = relationship(back_populates="relationship_")


class Evidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A piece of supporting evidence for a Relationship.

    Points at the content unit (and optionally a text excerpt within it)
    that grounds the relationship, so claims stay traceable to source text
    rather than being presented as bare assertions.
    """

    __tablename__ = "evidence"

    relationship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("relationships.id"), index=True
    )
    content_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_units.id"), index=True
    )
    excerpt: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    relationship_: Mapped["Relationship"] = relationship(back_populates="evidence")
