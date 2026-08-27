"""Integration tests for the get-trip endpoint."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_principal, get_trip_service
from app.api.exception_handlers import trip_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.trips import router
from app.auth.service import AuthenticatedPrincipal
from app.database.models.trip import Trip
from app.database.models.user import User
from app.domain.errors import TripError, TripNotFoundError
from app.services.trip_service import TripService


def create_get_app(
    trip_service: MagicMock,
    principal: MagicMock,
) -> FastAPI:
    """Create an isolated application containing the trip routes."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(TripError, trip_exception_handler)
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_trip_service] = lambda: trip_service
    application.dependency_overrides[get_current_principal] = lambda: principal
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


def test_get_trip_returns_authenticated_users_trip() -> None:
    """A user-owned trip should be returned as its public representation."""

    principal = create_principal()
    timestamp = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    trip = Trip(
        id=uuid4(),
        user_id=principal.user.id,
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
        status="draft",
        created_at=timestamp,
        updated_at=timestamp,
    )
    trip_service = MagicMock(spec=TripService)
    trip_service.get_trip = AsyncMock(return_value=trip)
    application = create_get_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.get(f"/api/v1/trips/{trip.id}")

    assert response.status_code == 200
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
    trip_service.get_trip.assert_awaited_once_with(
        trip_id=trip.id,
        user_id=principal.user.id,
    )
    assert "user_id" not in response.json()


def test_get_trip_returns_safe_not_found_response() -> None:
    """Missing or differently owned trips should use the same safe response."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.get_trip = AsyncMock(
        side_effect=TripNotFoundError("Trip was not found")
    )
    application = create_get_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.get(f"/api/v1/trips/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "trip_not_found"
    assert response.json()["error"]["message"] == "Trip was not found"


def test_get_trip_rejects_invalid_uuid_before_service_call() -> None:
    """An invalid path identifier should not reach the trip service."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.get_trip = AsyncMock()
    application = create_get_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.get("/api/v1/trips/not-a-uuid")

    assert response.status_code == 422
    trip_service.get_trip.assert_not_awaited()
