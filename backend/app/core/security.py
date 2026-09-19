"""Password hashing and session tokens, using only the standard library.

No new dependency is introduced for this. `hashlib.scrypt` is a memory-hard
KDF built into CPython, which is what a password needs; adding passlib or
bcrypt would pull a dependency to reach the same place.

Two different problems, deliberately solved two different ways:

  Passwords     Low entropy, chosen by humans, so the only defence against
                an offline attack is making each guess expensive. scrypt
                with a per-password random salt, work factors encoded into
                the stored string so they can be raised later without
                invalidating existing hashes.

  Session       256 bits from `secrets`, so guessing is not a threat model
  tokens        and a slow KDF would only cost latency on every request.
                What they do need is to be unreadable at rest, so only a
                SHA-256 digest is stored. The plaintext exists once, in the
                login response, and is never written down.

Verification is constant-time throughout (`hmac.compare_digest`), so neither
a password check nor a token lookup leaks information through timing.
"""

import base64
import hashlib
import hmac
import secrets

# scrypt cost parameters. n is the CPU/memory cost, and at 2**14 with r=8
# this needs ~16MB per hash -- enough to make bulk offline guessing painful,
# small enough that a login stays well under a tenth of a second here.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

TOKEN_BYTES = 32  # 256 bits
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # two weeks

# CPython refuses scrypt above this without raising maxmem explicitly.
_MAXMEM = 132 * 1024 * 1024

MIN_PASSWORD_LENGTH = 10


class InvalidPasswordHashError(ValueError):
    """A stored hash is not in a form this module can verify."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=SCRYPT_DKLEN, maxmem=_MAXMEM
    )


def hash_password(password: str) -> str:
    """Return a self-describing `scrypt$n$r$p$salt$hash` string.

    The parameters travel with the hash so raising them later leaves old
    hashes verifiable -- each one still states how it was made.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    derived = _derive(password, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
    except (ValueError, AttributeError) as exc:
        raise InvalidPasswordHashError("stored password hash is malformed") from exc

    if scheme != "scrypt":
        raise InvalidPasswordHashError(f"unsupported password hash scheme {scheme!r}")

    try:
        derived = _derive(password, _unb64(salt_b64), n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError) as exc:
        raise InvalidPasswordHashError("stored password hash is malformed") from exc

    return hmac.compare_digest(derived, _unb64(hash_b64))


def generate_session_token() -> str:
    """A fresh opaque bearer token. Returned to the client exactly once."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """The digest stored for a token. Deterministic, so lookup is an index hit."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
