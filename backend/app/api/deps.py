from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import User
from app.services import auth_service

# Sent on every 401 so a client knows what scheme to retry with.
_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _bearer_token(request: Request) -> str | None:
    """Pull the token out of `Authorization: Bearer <token>`.

    Read only from the header. A token in a query string would end up in
    logs, browser history and referrers, so that form is not accepted even
    as a convenience.
    """
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """The authenticated user, or 401.

    **This is the isolation boundary.** Every user-scoped route takes its
    `user_id` from here and never from the path, query or body, so a client
    cannot address another user's data by changing a parameter.
    """
    token = _bearer_token(request)
    if token is None:
        raise _UNAUTHENTICATED

    user = await auth_service.resolve_session(db, token)
    if user is None:
        raise _UNAUTHENTICATED

    # `resolve_session` stamps last_used_at; commit so it is not lost when
    # the request itself makes no other write.
    await db.commit()
    return user


async def get_current_user_optional(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User | None:
    """The authenticated user, or None -- never a 401.

    For endpoints that serve canonical content to everyone and merely
    *enrich* it for a signed-in caller. The canonical half of the response is
    identical either way; a bad or missing token simply means no user state,
    not a refusal.
    """
    token = _bearer_token(request)
    if token is None:
        return None

    user = await auth_service.resolve_session(db, token)
    if user is None:
        return None

    await db.commit()
    return user


async def get_current_token(request: Request) -> str:
    """The raw bearer token, for logout."""
    token = _bearer_token(request)
    if token is None:
        raise _UNAUTHENTICATED
    return token


__all__ = [
    "get_db",
    "get_current_user",
    "get_current_user_optional",
    "get_current_token",
]
