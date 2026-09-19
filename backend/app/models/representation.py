"""Derived semantic representations of source text.

A `ContextualPassage` is **not a source document**. It is a computational
representation built by concatenating adjacent `ContentUnit`s so the
embedding model has enough context to work with. The ContentUnits remain
authoritative: they keep the extracted structure, the original text, the
ordering and the provenance, and nothing here replaces or merges them.

The separation matters for provenance. A search hit on a passage must be
traceable back to the exact source units that produced it, and must never
be presented as though it were a unit the source actually contained.
"""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

OWNER_TYPE_CONTEXTUAL_PASSAGE = "contextual_passage"


class ContextualPassage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An ordered group of adjacent ContentUnits, built for embedding.

    Identity is (container, sequence_number, grouping_config): two grouping
    configurations can coexist so they can be compared, rather than one
    silently overwriting the other.
    """

    __tablename__ = "contextual_passages"
    __table_args__ = (
        UniqueConstraint(
            "container_id",
            "sequence_number",
            "grouping_config",
            name="uq_contextual_passage_identity",
        ),
    )

    container_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("containers.id"), index=True
    )
    # Order of this passage within its container.
    sequence_number: Mapped[int] = mapped_column(Integer)

    # The contributing ContentUnits, in source order. This is the traceability
    # record: every passage can name exactly what produced it.
    source_unit_ids: Mapped[list] = mapped_column(JSONB)
    first_unit_sequence: Mapped[int] = mapped_column(Integer)
    last_unit_sequence: Mapped[int] = mapped_column(Integer)
    unit_count: Mapped[int] = mapped_column(Integer)

    # Exactly the text handed to the model. Stored rather than reconstructed
    # on demand: this is an experiment, and being able to see what was
    # actually embedded is the point. Reconstruction would also depend on the
    # grouping code never changing, which is what the hash exists to detect.
    text_content: Mapped[str] = mapped_column(Text)
    # Inherited from the contributing units, so tier filtering works
    # identically for both representations.
    text_tier: Mapped[str] = mapped_column(String(16), index=True)

    # e.g. "window=3;overlap=1;max_tokens=240". Part of the identity and of
    # the hash, so changing the grouping rule invalidates old passages.
    grouping_config: Mapped[str] = mapped_column(String(64), index=True)
    # SHA-256 over the grouping config plus the ordered prepared unit texts:
    # changes if the text changes, the order changes, or the config changes.
    source_hash: Mapped[str] = mapped_column(String(64), index=True)

    container: Mapped["Container"] = relationship()  # noqa: F821
