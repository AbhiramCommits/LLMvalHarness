"""API key authentication.

Keys are stored in Postgres as salted PBKDF2-SHA256 hashes -- the raw key is
never persisted. A cheap ``key_prefix`` (first 16 hex chars of SHA-256 of the
raw key) is indexed so verification only hashes a handful of candidate rows.

Role gating is enforced with the :func:`require_role` FastAPI dependency (one
per router), never with inline checks in handlers.
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request, status

from app.models.enums import ApiKeyRole

PBKDF2_ITERATIONS = 100_000

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Authentication required",
)


@dataclass(frozen=True)
class AuthContext:
    api_key_id: UUID
    name: str
    role: ApiKeyRole


def hash_api_key(raw_key: str) -> str:
    """Return the stored representation of an API key: pbkdf2_sha256$iter$salt$hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw_key.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def key_prefix(raw_key: str) -> str:
    """Index-friendly prefix of a raw key (first 16 hex chars of SHA-256)."""
    return hashlib.sha256(raw_key.encode()).hexdigest()[:16]


def verify_api_key(raw_key: str, stored: str) -> bool:
    try:
        _algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            raw_key.encode(),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def get_auth_context(request: Request) -> AuthContext | None:
    return getattr(request.state, "auth", None)


def require_role(*roles: ApiKeyRole) -> Callable[[Request], None]:
    """FastAPI dependency: require the request's API key to have one of ``roles``."""

    def _require(request: Request) -> None:
        context = get_auth_context(request)
        if context is None:
            raise _UNAUTHORIZED
        if context.role not in roles:
            allowed = ", ".join(role.value for role in roles)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role in {{{allowed}}}",
            )

    return _require
