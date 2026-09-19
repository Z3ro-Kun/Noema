import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# The semantic role of a unit's text -- deliberately a tiny controlled
# vocabulary, not a per-source taxonomy. Which source it came from is
# TextSource's job; this says what kind of text it is.
TEXT_TIER_PRIMARY = "primary"  # the work's own words
TEXT_TIER_SUMMARY = "summary"  # a third party describing the work
TEXT_TIERS = (TEXT_TIER_PRIMARY, TEXT_TIER_SUMMARY)


class Container(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A chapter (literature/manhwa) or episode (anime) within a work."""

    __tablename__ = "containers"

    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id"), index=True
    )
    # "chapter" | "episode"
    container_type: Mapped[str] = mapped_column(String(32))
    sequence_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(512))
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)

    work: Mapped["Work"] = relationship(back_populates="containers")  # noqa: F821
    content_units: Mapped[list["ContentUnit"]] = relationship(back_populates="container")


class ContentUnit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """The smallest addressable unit of content: a passage, scene, or panel.

    This is the level the semantic layer (embeddings, entities, concepts)
    ultimately operates over, regardless of domain.

    `text_tier` keeps two genuinely different things apart: a paragraph of a
    novel *is* the narrative, while a Wikipedia plot summary is *about* it.
    Both are text worth embedding, but comparing them without knowing which
    is which produces a similarity score with no defensible meaning.
    """

    __tablename__ = "content_units"
    __table_args__ = (
        CheckConstraint(
            "text_tier IN ('primary', 'summary')", name="ck_content_unit_text_tier"
        ),
    )

    container_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("containers.id"), index=True
    )
    # "passage" | "scene" | "panel" | "dialogue" ...
    unit_type: Mapped[str] = mapped_column(String(32))
    sequence_number: Mapped[int] = mapped_column(Integer)
    text_content: Mapped[str | None] = mapped_column(Text)
    text_tier: Mapped[str] = mapped_column(
        String(16), default=TEXT_TIER_PRIMARY, server_default=TEXT_TIER_PRIMARY, index=True
    )
    # Null for text whose provenance is carried by the Work (literature
    # ingested from a single file); set for anything retrieved per-document.
    text_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("text_sources.id"), index=True
    )
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)

    container: Mapped["Container"] = relationship(back_populates="content_units")
    text_source: Mapped["TextSource | None"] = relationship()  # noqa: F821
