"""Integration tests for authentication exception responses."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
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


def create_error_app(error: AuthenticationError) -> FastAPI:
    """Create an application whose endpoint raises one domain error."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )

    @application.get("/raise-auth-error")
    async def raise_auth_error() -> None:
        raise error

    return application


@pytest.mark.parametrize(
    (
        "error_type",
        "expected_status",
        "expected_code",
        "expected_message",
        "expects_bearer_challenge",
    ),
    [
        (
            EmailAlreadyRegisteredError,
            409,
            "email_already_registered",
            "An account with this email already exists",
            False,
        ),
        (
            InvalidCredentialsError,
            401,
            "invalid_credentials",
            "Invalid email or password",
            True,
        ),
        (
            InvalidAccessTokenError,
            401,
            "invalid_access_token",
            "Invalid or expired access token",
            True,
        ),
        (
            InvalidRefreshTokenError,
            401,
            "invalid_refresh_token",
            "Invalid or expired refresh token",
            True,
        ),
        (
            SessionRevokedError,
            401,
            "session_revoked",
            "Authentication session is no longer active",
            True,
        ),
        (
            RefreshTokenReuseError,
            401,
            "refresh_token_reuse",
            "Authentication is required",
            True,
        ),
        (
            AccountNotActiveError,
            403,
            "account_not_active",
            "User account is not active",
            False,
        ),
    ],
)
def test_authentication_errors_have_safe_http_responses(
    error_type: type[AuthenticationError],
    expected_status: int,
    expected_code: str,
    expected_message: str,
    expects_bearer_challenge: bool,
) -> None:
    """Known domain errors should map to stable and safe HTTP responses."""

    request_id = str(uuid4())
    sensitive_internal_message = "sensitive internal authentication detail"
    application = create_error_app(error_type(sensitive_internal_message))

    with TestClient(application) as client:
        response = client.get(
            "/raise-auth-error",
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == expected_status
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": expected_message,
            "request_id": request_id,
        }
    }
    assert sensitive_internal_message not in response.text
    assert response.headers["X-Request-ID"] == request_id

    if expects_bearer_challenge:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    else:
        assert "WWW-Authenticate" not in response.headers


def test_unknown_authentication_error_uses_safe_fallback() -> None:
    """An unmapped authentication error should use the generic response."""

    request_id = str(uuid4())
    application = create_error_app(
        AuthenticationError("detail that must remain private")
    )

    with TestClient(application) as client:
        response = client.get(
            "/raise-auth-error",
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "authentication_failed",
            "message": "Authentication failed",
            "request_id": request_id,
        }
    }
    assert "detail that must remain private" not in response.text
