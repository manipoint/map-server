"""Integration tests for authenticated FastAPI dependencies."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    CurrentPrincipal,
    get_auth_service,
)
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthService


def create_protected_app(
    auth_service: MagicMock,
) -> FastAPI:
    """Create an application with one principal-protected endpoint."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.dependency_overrides[get_auth_service] = lambda: auth_service

    @application.get("/protected")
    async def protected_endpoint(
        principal: CurrentPrincipal,
    ) -> dict[str, str]:
        return {"user_id": str(principal.user.id)}

    return application


def test_protected_endpoint_rejects_missing_bearer_token() -> None:
    """A protected endpoint should return the standard authorization error."""

    request_id = str(uuid4())
    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()
    application = create_protected_app(auth_service)

    with TestClient(application) as client:
        response = client.get(
            "/protected",
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "error": {
            "code": "invalid_access_token",
            "message": "Invalid or expired access token",
            "request_id": request_id,
        }
    }
    auth_service.authenticate_access_token.assert_not_awaited()
