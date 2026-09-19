"""Provenance and rights for externally retrieved text.

Rights attach to the *retrieved document*, not to a Work. One anime work can
end up holding an AniList synopsis, a Wikipedia episode summary, and (later)
something from another source, each under different terms. Storing licence
state per Work would make "what must I attribute?" unanswerable.

These fields record what the source told us at retrieval time. They are an
ingestion-time reading of source-declared licensing, not a legal conclusion:
publishing or redistributing derived data may need separate review.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TextSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One retrieved document, with the rights that came with it.

    Identity is (source_name, source_ref, revision_ref, content_hash): a new
    revision of the same page is a *new* row, so retrieval history is kept
    rather than overwritten. Re-retrieving an unchanged revision matches the
    existing row and creates nothing.
    """

    __tablename__ = "text_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "source_ref",
            "revision_ref",
            "content_hash",
            name="uq_text_source_identity",
        ),
    )

    # "wikipedia" | "user_upload" | ...
    source_name: Mapped[str] = mapped_column(String(64), index=True)
    # Stable identifier within the source: a Wikipedia page title, a filename.
    source_ref: Mapped[str] = mapped_column(String(512), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    # Revision/version identifier where the source exposes one (a MediaWiki
    # revid). Null when the source has no version concept.
    revision_ref: Mapped[str | None] = mapped_column(String(128))
    # SHA-256 of the normalized retrieved text, so a silent edit is detectable
    # even if the source reports no revision.
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Licence as declared by the source at retrieval time.
    licence: Mapped[str] = mapped_column(String(64))
    licence_url: Mapped[str | None] = mapped_column(Text)
    # Rendered ready to display, not assembled later from parts that may drift.
    attribution_text: Mapped[str | None] = mapped_column(Text)
    requires_attribution: Mapped[bool] = mapped_column(Boolean, default=False)
    share_alike: Mapped[bool] = mapped_column(Boolean, default=False)
    # Gate for persistence. False whenever the licence could not be established:
    # unknown must never be treated as permitted.
    permits_storage: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # How that assessment was reached, so a later reader can re-check it.
    rights_basis: Mapped[str | None] = mapped_column(Text)

    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)
