"""Integration tests for the list-trips endpoint."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_principal, get_trip_service
from app.api.exception_handlers import invalid_cursor_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.trips import router
from app.auth.service import AuthenticatedPrincipal
from app.common.exceptions import InvalidCursorError
from app.database.models.trip import Trip
from app.database.models.user import User
from app.domain.trips import TripStatus
from app.services.trip_service import TripListResult, TripService


def create_list_app(
    trip_service: MagicMock,
    principal: MagicMock,
) -> FastAPI:
    """Create an isolated application containing the trip routes."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        InvalidCursorError,
        invalid_cursor_exception_handler,
    )
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


def create_trip(*, user_id: object, status: str = "draft") -> Trip:
    """Create a complete persisted trip response fixture."""

    timestamp = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    return Trip(
        id=uuid4(),
        user_id=user_id,
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_list_trips_forwards_filters_and_returns_next_cursor() -> None:
    """Query filters and caller identity should reach the trip service."""

    principal = create_principal()
    trip = create_trip(user_id=principal.user.id, status="planned")
    trip_service = MagicMock(spec=TripService)
    trip_service.list_trips = AsyncMock(
        return_value=TripListResult(
            items=[trip],
            next_cursor="next-page-cursor",
        )
    )
    application = create_list_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/trips",
            params={
                "status": "planned",
                "limit": 10,
                "cursor": "current-page-cursor",
            },
        )

    assert response.status_code == 200
    assert response.json()["next_cursor"] == "next-page-cursor"
    assert response.json()["items"] == [
        {
            "id": str(trip.id),
            "title": "London museums",
            "origin": "Lahore",
            "destination": "London",
            "origin_location": None,
            "destination_location": None,
            "start_date": "2026-09-10",
            "end_date": "2026-09-15",
            "status": "planned",
            "created_at": "2026-08-27T12:00:00Z",
            "updated_at": "2026-08-27T12:00:00Z",
        }
    ]
    trip_service.list_trips.assert_awaited_once_with(
        user_id=principal.user.id,
        status=TripStatus.PLANNED,
        limit=10,
        cursor="current-page-cursor",
    )


def test_list_trips_uses_safe_defaults() -> None:
    """Omitted query parameters should use the first-page defaults."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.list_trips = AsyncMock(
        return_value=TripListResult(items=[], next_cursor=None)
    )
    application = create_list_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.get("/api/v1/trips")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    trip_service.list_trips.assert_awaited_once_with(
        user_id=principal.user.id,
        status=None,
        limit=20,
        cursor=None,
    )


def test_list_trips_rejects_invalid_query_before_service_call() -> None:
    """Unsupported statuses and excessive page sizes should be rejected."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.list_trips = AsyncMock()
    application = create_list_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/trips",
            params={"status": "deleted", "limit": 51},
        )

    assert response.status_code == 422
    trip_service.list_trips.assert_not_awaited()


def test_list_trips_returns_safe_error_for_invalid_cursor() -> None:
    """A malformed pagination cursor should receive the public cursor error."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.list_trips = AsyncMock(
        side_effect=InvalidCursorError("Malformed cursor")
    )
    application = create_list_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/trips",
            params={"cursor": "malformed-cursor"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"
    assert "Malformed cursor" not in response.text
