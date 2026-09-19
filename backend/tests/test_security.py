"""Password hashing and session tokens. Pure functions; no database, no network."""

import pytest

from app.core.security import (
    MIN_PASSWORD_LENGTH,
    InvalidPasswordHashError,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)

PASSWORD = "correct horse battery staple"


# --- password hashing ----------------------------------------------------


def test_hash_is_not_the_password() -> None:
    stored = hash_password(PASSWORD)

    assert PASSWORD not in stored
    assert stored.startswith("scrypt$")


def test_correct_password_verifies() -> None:
    assert verify_password(PASSWORD, hash_password(PASSWORD)) is True


def test_wrong_password_does_not_verify() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password("wrong password entirely", stored) is False
    # A near miss is still a miss.
    assert verify_password(PASSWORD + "!", stored) is False
    assert verify_password(PASSWORD.upper(), stored) is False


def test_the_same_password_hashes_differently_every_time() -> None:
    """Per-hash random salt: two users with one password share no hash."""
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)

    assert first != second
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)


def test_work_factors_travel_with_the_hash() -> None:
    """A hash states how it was made, so the cost can be raised later."""
    scheme, n, r, p, salt, digest = hash_password(PASSWORD).split("$")

    assert scheme == "scrypt"
    assert int(n) >= 2**14 and int(r) >= 8 and int(p) >= 1
    assert salt and digest


def test_a_hash_made_at_lower_cost_still_verifies() -> None:
    """The upgrade path: old rows must not become unverifiable."""
    import hashlib
    import secrets

    from app.core.security import _b64

    salt = secrets.token_bytes(16)
    weak = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=2**10, r=8, p=1, dklen=32)
    stored = f"scrypt$1024$8$1${_b64(salt)}${_b64(weak)}"

    assert verify_password(PASSWORD, stored) is True
    assert verify_password("nope", stored) is False


@pytest.mark.parametrize("stored", ["", "not-a-hash", "scrypt$1$2", "bcrypt$a$b$c$d$e"])
def test_malformed_stored_hashes_are_rejected_loudly(stored: str) -> None:
    """A corrupt hash must raise, never quietly return False as if wrong."""
    with pytest.raises(InvalidPasswordHashError):
        verify_password(PASSWORD, stored)


def test_minimum_password_length_is_enforced_somewhere_sensible() -> None:
    assert MIN_PASSWORD_LENGTH >= 8


# --- session tokens ------------------------------------------------------


def test_tokens_are_unique_and_long() -> None:
    tokens = {generate_session_token() for _ in range(200)}

    assert len(tokens) == 200
    assert all(len(token) >= 40 for token in tokens)


def test_token_hash_is_deterministic_and_hides_the_token() -> None:
    token = generate_session_token()
    digest = hash_session_token(token)

    assert digest == hash_session_token(token)
    assert token not in digest
    assert len(digest) == 64


def test_different_tokens_hash_differently() -> None:
    assert hash_session_token(generate_session_token()) != hash_session_token(
        generate_session_token()
    )
