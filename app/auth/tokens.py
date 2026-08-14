"""Access and refresh token utilities."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import InvalidTokenError

from app.auth.exceptions import InvalidAccessTokenError
from app.config import Settings


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Validated claims extracted from an access token."""

    user_id: UUID
    session_id: UUID
    token_id: UUID
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    """Newly issued signed access token."""

    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    """New opaque refresh token and its server-side hash."""

    token: str = field(repr=False)
    token_hash: str = field(repr=False)
    expires_at: datetime


def create_access_token(
    *,
    user_id: UUID,
    session_id: UUID,
    settings: Settings,
    issued_at: datetime | None = None,
) -> IssuedAccessToken:
    """Create a short-lived signed access token."""
    issued_at = issued_at or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.access_token_ttl_minutes)

    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(uuid4()),
        "iat": issued_at,
        "exp": expires_at,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "token_type": "access",
    }
    token = jwt.encode(
        payload,
        settings.jwt_signing_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return IssuedAccessToken(token=token, expires_at=expires_at)


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    """Validate an access token and return trusted claims."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": [
                    "sub",
                    "sid",
                    "jti",
                    "iat",
                    "exp",
                    "iss",
                    "aud",
                    "token_type",
                ]
            },
        )
        if payload["token_type"] != "access":
            raise InvalidAccessTokenError("Invalid token type")
        return AccessTokenClaims(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            token_id=UUID(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except InvalidAccessTokenError:
        raise
    except (InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise InvalidAccessTokenError("Invalid access token") from error


def hash_refresh_token(token: str, settings: Settings) -> str:
    """Create a deterministic keyed hash for refresh-token lookup."""
    return hmac.new(
        settings.refresh_token_hash_key.get_secret_value().encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def create_refresh_token(
    settings: Settings,
    *,
    issued_at: datetime | None = None,
) -> IssuedRefreshToken:
    """Create a high-entropy opaque refresh token."""
    issued_at = issued_at or datetime.now(UTC)
    expires_at = issued_at + timedelta(days=settings.refresh_token_ttl_days)
    token = secrets.token_urlsafe(48)

    return IssuedRefreshToken(
        token=token,
        token_hash=hash_refresh_token(token, settings),
        expires_at=expires_at,
    )
