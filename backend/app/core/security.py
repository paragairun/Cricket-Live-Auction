"""Password hashing and JWT handling.

Version: 2.0.0 (2026-11-20)

Changed from 1.x: dropped passlib. `passlib 1.7.4` (its last release, from 2020)
reads `bcrypt.__about__.__version__`, which no longer exists in bcrypt 4.1+, and
then raises on any password at all. The pinned pair in 1.x was passlib 1.7.4
with bcrypt 4.0.1 — a combination that only works on that exact old bcrypt and
fails the moment the dependency is bumped. This module now calls bcrypt
directly. Existing hashes keep verifying: passlib produced standard `$2b$`
bcrypt hashes, which bcrypt.checkpw reads natively.

bcrypt ignores input past 72 bytes, so the truncation is explicit here rather
than a silent surprise.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# bcrypt's hard input limit.
_MAX_PASSWORD_BYTES = 72


def _prepare(password: str) -> bytes:
    """Encode and truncate to bcrypt's 72-byte limit."""
    return password.encode("utf-8")[:_MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed or legacy hash — treat as a failed login, never an error.
        return False


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return jwt.encode(
        {"sub": subject, "exp": expire, "iat": now, "typ": "access"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_token(token: str) -> Optional[str]:
    """Return the subject, or None when the token is unusable.

    Also checks the token type, so a token minted for another purpose cannot be
    presented as an access token.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None

    if payload.get("typ") not in (None, "access"):
        return None

    subject = payload.get("sub")
    if subject is None:
        return None
    return str(subject)
