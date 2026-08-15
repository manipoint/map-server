"""HTTP exception handlers for application-domain errors."""

from dataclasses import dataclass

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.auth.exceptions import (
    AccountNotActiveError,
    AuthenticationError,
    EmailAlreadyRegisteredError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
    SessionRevokedError,
)


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """Safe HTTP representation of a domain error."""

    status_code: int
    code: str
    message: str


AUTH_ERROR_DEFINITIONS: dict[
    type[AuthenticationError],
    ErrorDefinition,
] = {
    EmailAlreadyRegisteredError: ErrorDefinition(
        status_code=status.HTTP_409_CONFLICT,
        code="email_already_registered",
        message="An account with this email already exists",
    ),
    InvalidCredentialsError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_credentials",
        message="Invalid email or password",
    ),
    InvalidAccessTokenError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_access_token",
        message="Invalid or expired access token",
    ),
    InvalidRefreshTokenError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_refresh_token",
        message="Invalid or expired refresh token",
    ),
    SessionRevokedError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="session_revoked",
        message="Authentication session is no longer active",
    ),
    RefreshTokenReuseError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="refresh_token_reuse",
        message="Authentication is required",
    ),
    AccountNotActiveError: ErrorDefinition(
        status_code=status.HTTP_403_FORBIDDEN,
        code="account_not_active",
        message="User account is not active",
    ),
}


async def authentication_exception_handler(
    request: Request,
    error: AuthenticationError,
) -> JSONResponse:
    """Convert an authentication-domain error into a safe response."""

    definition = AUTH_ERROR_DEFINITIONS.get(
        type(error),
        ErrorDefinition(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="authentication_failed",
            message="Authentication failed",
        ),
    )

    headers = None
    if definition.status_code == status.HTTP_401_UNAUTHORIZED:
        headers = {"WWW-Authenticate": "Bearer"}

    return JSONResponse(
        status_code=definition.status_code,
        headers=headers,
        content={
            "error": {
                "code": definition.code,
                "message": definition.message,
                "request_id": getattr(
                    request.state,
                    "request_id",
                    None,
                ),
            }
        },
    )
