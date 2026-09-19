"""Registration, login and session resolution, inside rolled-back transactions."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_session_token
from app.models import User, UserSession
from app.services import auth_service

PASSWORD = "a-sufficiently-long-password"


async def make_user(session: AsyncSession, email: str = "reader@example.test") -> User:
    return await auth_service.register_user(session, email=email, password=PASSWORD)


# --- registration --------------------------------------------------------


async def test_registration_creates_a_user_with_a_hashed_password(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)

    assert user.id is not None
    assert user.email == "reader@example.test"
    assert user.is_active is True
    # The password itself is never stored in any form that reveals it.
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("scrypt$")


async def test_email_is_normalized_so_one_address_is_one_account(
    db_session: AsyncSession,
) -> None:
    user = await auth_service.register_user(
        db_session, email="  Reader@Example.TEST  ", password=PASSWORD
    )

    assert user.email == "reader@example.test"
    # And the normalized form is what login matches on.
    assert await auth_service.get_user_by_email(db_session, "READER@EXAMPLE.TEST") is not None


async def test_duplicate_registration_is_refused(db_session: AsyncSession) -> None:
    await make_user(db_session)

    with pytest.raises(auth_service.EmailAlreadyRegisteredError):
        await auth_service.register_user(
            db_session, email="READER@example.test", password=PASSWORD
        )


async def test_short_passwords_are_refused_before_a_user_exists(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(auth_service.WeakPasswordError):
        await auth_service.register_user(db_session, email="x@example.test", password="short")

    assert await auth_service.get_user_by_email(db_session, "x@example.test") is None


@pytest.mark.parametrize("email", ["nope", "a@@b.test", "@b.test", "a@", "a b@c.test"])
async def test_implausible_emails_are_refused(db_session: AsyncSession, email: str) -> None:
    with pytest.raises(auth_service.InvalidEmailError):
        await auth_service.register_user(db_session, email=email, password=PASSWORD)


# --- authentication ------------------------------------------------------


async def test_correct_credentials_authenticate(db_session: AsyncSession) -> None:
    user = await make_user(db_session)

    assert (
        await auth_service.authenticate(db_session, email="reader@example.test", password=PASSWORD)
    ).id == user.id


async def test_wrong_password_is_refused(db_session: AsyncSession) -> None:
    await make_user(db_session)

    with pytest.raises(auth_service.InvalidCredentialsError):
        await auth_service.authenticate(
            db_session, email="reader@example.test", password="not the password"
        )


async def test_unknown_email_raises_the_same_error_as_a_wrong_password(
    db_session: AsyncSession,
) -> None:
    """Identical failure, so login cannot be used to enumerate accounts."""
    await make_user(db_session)

    with pytest.raises(auth_service.InvalidCredentialsError) as wrong_password:
        await auth_service.authenticate(
            db_session, email="reader@example.test", password="wrong"
        )
    with pytest.raises(auth_service.InvalidCredentialsError) as no_such_user:
        await auth_service.authenticate(
            db_session, email="nobody@example.test", password="wrong"
        )

    assert str(wrong_password.value) == str(no_such_user.value)


async def test_a_deactivated_account_cannot_authenticate(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    user.is_active = False
    await db_session.flush()

    with pytest.raises(auth_service.InvalidCredentialsError):
        await auth_service.authenticate(
            db_session, email="reader@example.test", password=PASSWORD
        )


# --- sessions ------------------------------------------------------------


async def test_the_plaintext_token_is_never_stored(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    row, token = await auth_service.create_session(db_session, user)

    assert row.token_hash == hash_session_token(token)
    assert token not in row.token_hash

    # Nothing anywhere in the sessions table holds the token itself.
    stored = (
        (await db_session.execute(select(UserSession.token_hash))).scalars().all()
    )
    assert token not in stored


async def test_a_valid_token_resolves_to_its_user(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, token = await auth_service.create_session(db_session, user)

    resolved = await auth_service.resolve_session(db_session, token)
    assert resolved is not None and resolved.id == user.id


@pytest.mark.parametrize("token", ["", "not-a-real-token", "x" * 43])
async def test_garbage_tokens_resolve_to_nobody(db_session: AsyncSession, token: str) -> None:
    await make_user(db_session)

    assert await auth_service.resolve_session(db_session, token) is None


async def test_an_expired_session_resolves_to_nobody(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    row, token = await auth_service.create_session(db_session, user)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.flush()

    assert await auth_service.resolve_session(db_session, token) is None


async def test_logout_revokes_the_token_immediately(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, token = await auth_service.create_session(db_session, user)

    assert await auth_service.revoke_session(db_session, token) is True
    assert await auth_service.resolve_session(db_session, token) is None
    # Revoking twice is not an error, and does not resurrect anything.
    assert await auth_service.revoke_session(db_session, token) is False


async def test_revoking_one_session_leaves_the_others_alone(db_session: AsyncSession) -> None:
    """Logging out on one device must not log the user out everywhere."""
    user = await make_user(db_session)
    _, phone = await auth_service.create_session(db_session, user)
    _, laptop = await auth_service.create_session(db_session, user)

    await auth_service.revoke_session(db_session, phone)

    assert await auth_service.resolve_session(db_session, phone) is None
    assert (await auth_service.resolve_session(db_session, laptop)).id == user.id


async def test_revoke_all_sessions_ends_every_live_one(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    _, first = await auth_service.create_session(db_session, user)
    _, second = await auth_service.create_session(db_session, user)

    assert await auth_service.revoke_all_sessions(db_session, user.id) == 2
    assert await auth_service.resolve_session(db_session, first) is None
    assert await auth_service.resolve_session(db_session, second) is None


async def test_one_users_token_never_resolves_to_another_user(
    db_session: AsyncSession,
) -> None:
    alice = await make_user(db_session, "alice@example.test")
    bob = await make_user(db_session, "bob@example.test")
    _, alice_token = await auth_service.create_session(db_session, alice)
    _, bob_token = await auth_service.create_session(db_session, bob)

    assert (await auth_service.resolve_session(db_session, alice_token)).id == alice.id
    assert (await auth_service.resolve_session(db_session, bob_token)).id == bob.id


async def test_deleting_a_user_takes_their_sessions_with_them(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    await auth_service.create_session(db_session, user)

    await db_session.delete(user)
    await db_session.flush()

    remaining = await db_session.execute(
        select(func.count()).select_from(UserSession).where(UserSession.user_id == user.id)
    )
    assert remaining.scalar_one() == 0
