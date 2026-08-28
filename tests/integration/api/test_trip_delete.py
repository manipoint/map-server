"""Integration tests for the delete-trip endpoint."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_current_principal,
    get_trip_service,
)
from app.api.exception_handlers import (
    authentication_exception_handler,
    trip_exception_handler,
)
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.trips import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.database.models.user import User
from app.domain.errors import TripError, TripNotFoundError
from app.services.trip_service import TripService


def create_delete_app(
    trip_service: MagicMock,
    *,
    principal: MagicMock | None,
) -> FastAPI:
    """Create an isolated application containing the trip routes."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.add_exception_handler(TripError, trip_exception_handler)
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_trip_service] = lambda: trip_service

    if principal is not None:
        application.dependency_overrides[get_current_principal] = lambda: principal
    else:
        auth_service = MagicMock(spec=AuthService)
        application.dependency_overrides[get_auth_service] = lambda: auth_service

    return application


def create_principal() -> MagicMock:
    """Create an authenticated principal for route tests."""

    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user = User(
        id=uuid4(),
        email="traveler@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    return principal


def test_delete_trip_returns_empty_no_content_response() -> None:
    """A successful permanent deletion should return an empty HTTP 204."""

    principal = create_principal()
    trip_id = uuid4()
    trip_service = MagicMock(spec=TripService)
    trip_service.delete_trip = AsyncMock(return_value=None)
    application = create_delete_app(trip_service, principal=principal)

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/trips/{trip_id}")

    assert response.status_code == 204
    assert response.content == b""
    trip_service.delete_trip.assert_awaited_once_with(
        trip_id=trip_id,
        user_id=principal.user.id,
    )


def test_delete_trip_returns_safe_not_found_response() -> None:
    """Missing and differently owned trips should use the same safe response."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.delete_trip = AsyncMock(
        side_effect=TripNotFoundError("Trip was not found")
    )
    application = create_delete_app(trip_service, principal=principal)

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/trips/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "trip_not_found"
    assert response.json()["error"]["message"] == "Trip was not found"


def test_delete_trip_rejects_invalid_uuid_before_service_call() -> None:
    """An invalid trip identifier should not reach the deletion service."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.delete_trip = AsyncMock()
    application = create_delete_app(trip_service, principal=principal)

    with TestClient(application) as client:
        response = client.delete("/api/v1/trips/not-a-uuid")

    assert response.status_code == 422
    trip_service.delete_trip.assert_not_awaited()


def test_delete_trip_requires_bearer_token() -> None:
    """An unauthenticated caller must not reach permanent deletion."""

    trip_service = MagicMock(spec=TripService)
    trip_service.delete_trip = AsyncMock()
    application = create_delete_app(trip_service, principal=None)

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/trips/{uuid4()}")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_access_token"
    trip_service.delete_trip.assert_not_awaited()
