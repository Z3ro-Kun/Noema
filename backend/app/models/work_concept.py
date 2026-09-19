"""Work-level concept associations: the content-feature side of taste.

`ContentConcept` links a concept to a single `ContentUnit` -- a concept
occurring in one passage. That granularity cannot say anything about a work
as a whole, and two of three domains (anime, manga/manhwa) have no primary
text at all, so an entire domain could never contribute to the shared
`Concept` vocabulary through it. This table is the work-level analogue.

    Work "Frankenstein" --< WorkConcept >-- Concept "Scientific Overreach"

Deliberately parallel to `ContentConcept` rather than a replacement for it:
the two answer different questions ("where does this appear?" vs. "what is
this work about?") and a later phase may well populate both.

**A row here is a characterization, not a fact about the world.** It says
that a named source, by a named method, associated this concept with this
work. `source` and `method` are non-null precisely so nothing can be read as
an unattributed claim, and `supporting_labels` keeps the original wording the
source actually used.

**Nothing here is user-specific.** A work's concepts are identical for every
user; a user's relationship to a work lives in `user_content_interactions`
and never touches this table. That separation is what will later allow
"which concepts does this user rate highly?" to be asked at all -- the two
sides have to be independently true before they can be correlated.
"""

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Mirrors `Relationship.source`: who is making this association.
SOURCE_PROVIDED = "source"  # an external catalogue said so
SOURCE_COMPUTED = "computed"  # our own pipeline derived it
SOURCE_USER = "user"  # a person asserted it
WORK_CONCEPT_SOURCES = (SOURCE_PROVIDED, SOURCE_COMPUTED, SOURCE_USER)

# How a source-provided association was derived.
METHOD_ANILIST_GENRE = "anilist_genre"
METHOD_ANILIST_TAG = "anilist_tag"
METHOD_GUTENBERG_SUBJECT = "gutenberg_subject"


class WorkConcept(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One concept associated with one work, with its provenance."""

    __tablename__ = "work_concepts"
    __table_args__ = (
        # One row per pair. Several source labels can support the same
        # concept (Death Note carries "Crime", "Detective" and "Police",
        # all meaning crime-and-investigation); they accumulate in
        # `supporting_labels` rather than becoming duplicate rows.
        UniqueConstraint("work_id", "concept_id", name="uq_work_concept"),
        CheckConstraint(
            "source IN ('source', 'computed', 'user')", name="ck_work_concept_source"
        ),
        # Confidence is optional, but when present it must be a real 0-1
        # value -- see the column comment for what it does and does not mean.
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_work_concept_confidence",
        ),
    )

    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id"), index=True
    )

    # Non-null: an association with no attribution is exactly the kind of
    # bare assertion this project does not make.
    source: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[str] = mapped_column(String(64), index=True)

    # **The source's own stated relevance, rescaled to 0-1.** AniList tags
    # carry a community `rank` (0-100); that, divided by 100, is what lands
    # here. It is NOT a probability, NOT a strength-of-theme measurement,
    # and NOT comparable across methods. NULL means the source stated no
    # relevance at all -- AniList genres and Gutenberg subjects are
    # unranked, and inventing a number for them would be fabrication.
    confidence: Mapped[float | None] = mapped_column(Float)

    # Every original label that supported this concept, each with the method
    # and rank it came with. Keeps provenance when several labels collapse
    # onto one concept, so nothing is silently overwritten.
    supporting_labels: Mapped[list | None] = mapped_column(JSONB)

    work: Mapped["Work"] = relationship()  # noqa: F821
    concept: Mapped["Concept"] = relationship()  # noqa: F821
