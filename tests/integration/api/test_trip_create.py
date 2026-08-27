"""Integration tests for the create-trip endpoint."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_current_principal,
    get_trip_service,
)
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.trips import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.database.models.trip import Trip
from app.database.models.user import User
from app.services.trip_service import TripService


def create_trip_app(
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
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_trip_service] = lambda: trip_service

    if principal is not None:
        application.dependency_overrides[get_current_principal] = lambda: principal
    else:
        auth_service = MagicMock(spec=AuthService)
        application.dependency_overrides[get_auth_service] = lambda: auth_service

    return application


def create_principal() -> MagicMock:
    """Create an authenticated principal with a persisted user identity."""

    user = User(
        id=uuid4(),
        email="traveler@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user = user
    return principal


def test_create_trip_returns_authenticated_users_draft() -> None:
    """A valid request should create and return the caller's draft trip."""

    principal = create_principal()
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    trip = Trip(
        id=uuid4(),
        user_id=principal.user.id,
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
        status="draft",
        created_at=now,
        updated_at=now,
    )
    trip_service = MagicMock(spec=TripService)
    trip_service.create_trip = AsyncMock(return_value=trip)
    application = create_trip_app(trip_service, principal=principal)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/trips",
            json={
                "title": "  London museums  ",
                "origin": "  Lahore  ",
                "destination": "  London  ",
                "start_date": "2026-09-10",
                "end_date": "2026-09-15",
            },
        )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(trip.id),
        "title": "London museums",
        "origin": "Lahore",
        "destination": "London",
        "start_date": "2026-09-10",
        "end_date": "2026-09-15",
        "status": "draft",
        "created_at": "2026-08-27T12:00:00Z",
        "updated_at": "2026-08-27T12:00:00Z",
    }
    trip_service.create_trip.assert_awaited_once_with(
        user_id=principal.user.id,
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
    )
    assert "user_id" not in response.json()


def test_create_trip_rejects_invalid_date_range_before_service_call() -> None:
    """An invalid date range should not reach the trip service."""

    trip_service = MagicMock(spec=TripService)
    trip_service.create_trip = AsyncMock()
    application = create_trip_app(trip_service, principal=create_principal())

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/trips",
            json={
                "destination": "London",
                "start_date": "2026-09-15",
                "end_date": "2026-09-10",
            },
        )

    assert response.status_code == 422
    trip_service.create_trip.assert_not_awaited()


def test_create_trip_rejects_client_supplied_user_id() -> None:
    """A caller must not be able to choose the trip owner."""

    trip_service = MagicMock(spec=TripService)
    trip_service.create_trip = AsyncMock()
    application = create_trip_app(trip_service, principal=create_principal())

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/trips",
            json={
                "user_id": str(uuid4()),
                "destination": "London",
                "start_date": "2026-09-10",
                "end_date": "2026-09-15",
            },
        )

    assert response.status_code == 422
    trip_service.create_trip.assert_not_awaited()


def test_create_trip_requires_bearer_token() -> None:
    """An unauthenticated request should be rejected before trip creation."""

    trip_service = MagicMock(spec=TripService)
    trip_service.create_trip = AsyncMock()
    application = create_trip_app(trip_service, principal=None)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/trips",
            json={
                "destination": "London",
                "start_date": "2026-09-10",
                "end_date": "2026-09-15",
            },
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_access_token"
    trip_service.create_trip.assert_not_awaited()
