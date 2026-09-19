"""Registration, login, and session resolution.

Kept out of the API layer for the same reason `catalog_service` is: routes
stay thin, and the same logic is reusable from a CLI or worker later.

Two behaviours worth stating because they are security properties, not
style:

  Registration and login report failure the same way regardless of whether
  the email exists, so neither endpoint can be used to enumerate accounts.
  Login also hashes a dummy password when the user is missing, so a
  non-existent account costs the same time as a wrong password.

  Sessions are resolved by digest, and an expired or revoked row is treated
  exactly like a token that was never issued.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    MIN_PASSWORD_LENGTH,
    SESSION_TTL_SECONDS,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.models import User, UserSession

# Hashed on a failed lookup so that "no such user" and "wrong password" take
# the same time. The value is irrelevant; the work it causes is the point.
_TIMING_DECOY_HASH = hash_password("timing-equalisation-decoy")


class AuthError(Exception):
    """Base class for anything the auth layer refuses to do."""


class EmailAlreadyRegisteredError(AuthError):
    """Raised on registration when the address is taken."""


class WeakPasswordError(AuthError):
    """Raised when a password does not meet the minimum policy."""


class InvalidCredentialsError(AuthError):
    """Raised on login. Deliberately says nothing about which part was wrong."""


class InvalidEmailError(AuthError):
    """The address is not a plausible identifier."""


def normalize_email(email: str) -> str:
    """Casefold and trim so one address cannot become two accounts."""
    return email.strip().lower()


def validate_email(email: str) -> str:
    """A sanity check, not RFC 5322 conformance.

    Nothing in this phase sends mail, so an address only needs to be a
    stable, normalizable identifier. Claiming to validate deliverability
    here would be a claim this system cannot back up.
    """
    normalized = normalize_email(email)
    local, separator, domain = normalized.partition("@")
    if not separator or not local or not domain or "@" in domain:
        raise InvalidEmailError("email address must look like name@domain")
    if "." not in domain or any(ch.isspace() for ch in normalized):
        raise InvalidEmailError("email address must look like name@domain")
    return normalized


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def register_user(
    session: AsyncSession, *, email: str, password: str, display_name: str | None = None
) -> User:
    """Create an account. Flushes but does not commit; the caller owns the transaction."""
    validate_password(password)
    normalized = validate_email(email)

    if await get_user_by_email(session, normalized) is not None:
        raise EmailAlreadyRegisteredError("that email address is already registered")

    user = User(
        email=normalized,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    """Return the user for these credentials, or raise InvalidCredentialsError."""
    user = await get_user_by_email(session, email)

    if user is None:
        # Spend the same work as a real verification so the absence of an
        # account is not observable through response time.
        verify_password(password, _TIMING_DECOY_HASH)
        raise InvalidCredentialsError("invalid email or password")

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("invalid email or password")

    if not user.is_active:
        raise InvalidCredentialsError("invalid email or password")

    return user


async def create_session(
    session: AsyncSession, user: User, *, ttl_seconds: int = SESSION_TTL_SECONDS
) -> tuple[UserSession, str]:
    """Issue a session. Returns the row and the plaintext token, which is
    the only time the token exists outside the client."""
    token = generate_session_token()
    row = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    await session.flush()
    return row, token


async def resolve_session(session: AsyncSession, token: str) -> User | None:
    """The active user for a bearer token, or None.

    Expired and revoked sessions return None rather than raising, so the
    caller cannot accidentally distinguish them in a response.
    """
    if not token:
        return None

    result = await session.execute(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at <= datetime.now(timezone.utc):
        return None

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None

    row.last_used_at = datetime.now(timezone.utc)
    return user


async def revoke_session(session: AsyncSession, token: str) -> bool:
    """Revoke one token. True if a live session was actually revoked."""
    result = await session.execute(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return False

    row.revoked_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every live session for a user. Returns how many were revoked."""
    rows = (
        (
            await session.execute(
                select(UserSession).where(
                    UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.revoked_at = now
    await session.flush()
    return len(rows)
