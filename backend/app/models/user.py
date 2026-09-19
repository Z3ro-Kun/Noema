"""Users and their sessions.

The minimum needed to safely attribute an interaction to someone. There is
deliberately no profile, no roles, no OAuth, no email verification and no
password reset: none of those are required to establish the data boundary
this phase exists to establish, and each one is a real surface to get wrong.

Password hashes live here as a single self-describing string
(`scrypt$n$r$p$salt$hash`) rather than as separate columns, so the work
factors travel with the hash and can be raised later without a migration --
an old hash still says how to verify itself.

Sessions are opaque random tokens, stored only as a SHA-256 digest. The
plaintext token is returned once, at login, and never persisted. That makes
a database disclosure insufficient to impersonate anyone, and makes logout a
real revocation rather than a client-side deletion.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Someone who can hold a library. Owns no canonical content."""

    __tablename__ = "users"

    # Stored casefolded so "A@b.com" and "a@b.com" cannot become two accounts.
    # Normalization happens in the service; the unique index enforces it.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str | None] = mapped_column(String(128))
    # Lets an account be disabled without deleting the interaction history,
    # which is evidence the taste layer will eventually want.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One bearer token's server-side record, revocable and expiring."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # SHA-256 of the token. A token is 256 bits of `secrets` entropy, so it
    # has no brute-force surface the way a password does and does not need a
    # slow KDF -- it needs to not be readable at rest, which this achieves.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="sessions")
