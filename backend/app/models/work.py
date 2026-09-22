import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Domain(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One of the top-level narrative domains: literature, anime, manhwa."""

    __tablename__ = "domains"

    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)

    works: Mapped[list["Work"]] = relationship(back_populates="domain")


class Creator(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An author, director, illustrator, or studio credited on one or more works."""

    __tablename__ = "creators"

    name: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str | None] = mapped_column(String(64))
    external_ids: Mapped[dict | None] = mapped_column(JSONB)

    works: Mapped[list["WorkCreator"]] = relationship(back_populates="creator")


class Work(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single literary work, anime series, or manhwa series."""

    __tablename__ = "works"

    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(512), index=True)
    original_title: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)

    # Where this work's record came from: "user_upload", "anilist", "jikan",
    # "public_domain", etc. Distinct from how relationships/facts about its
    # content are sourced (see Relationship.source).
    source: Mapped[str | None] = mapped_column(String(64))
    external_ids: Mapped[dict | None] = mapped_column(JSONB)
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)

    domain: Mapped["Domain"] = relationship(back_populates="works")
    creators: Mapped[list["WorkCreator"]] = relationship(back_populates="work")
    containers: Mapped[list["Container"]] = relationship(back_populates="work")
    # Units that describe the work as a whole rather than any one container.
    # Empty for nearly every work: see `ContentUnit`.
    content_units: Mapped[list["ContentUnit"]] = relationship(  # noqa: F821
        back_populates="work"
    )


class WorkCreator(Base):
    """Many-to-many association between works and their creators."""

    __tablename__ = "work_creators"
    __table_args__ = (UniqueConstraint("work_id", "creator_id", "role"),)

    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id"), primary_key=True
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("creators.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(64), primary_key=True, default="author")

    work: Mapped["Work"] = relationship(back_populates="creators")
    creator: Mapped["Creator"] = relationship(back_populates="works")
