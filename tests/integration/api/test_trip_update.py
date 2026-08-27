"""Integration tests for the update-trip endpoint."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_principal, get_trip_service
from app.api.exception_handlers import trip_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.trips import router
from app.api.schemas.trips import TripUpdateRequest
from app.auth.service import AuthenticatedPrincipal
from app.database.models.trip import Trip
from app.database.models.user import User
from app.domain.errors import TripError, TripNotFoundError
from app.services.trip_service import TripService


def create_update_app(
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


def create_updated_trip(*, user_id: object) -> Trip:
    """Create a complete updated trip returned by the service."""

    timestamp = datetime(2026, 8, 27, 12, 30, tzinfo=UTC)
    return Trip(
        id=uuid4(),
        user_id=user_id,
        title="Updated London trip",
        origin=None,
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 17),
        status="draft",
        created_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        updated_at=timestamp,
    )


def test_update_trip_forwards_only_explicit_partial_changes() -> None:
    """The route should preserve explicit fields, including nullable fields."""

    principal = create_principal()
    trip = create_updated_trip(user_id=principal.user.id)
    trip_service = MagicMock(spec=TripService)
    trip_service.update_trip = AsyncMock(return_value=trip)
    application = create_update_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.patch(
            f"/api/v1/trips/{trip.id}",
            json={
                "title": "  Updated London trip  ",
                "origin": None,
                "end_date": "2026-09-17",
            },
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Updated London trip"
    assert response.json()["origin"] is None
    assert response.json()["end_date"] == "2026-09-17"

    trip_service.update_trip.assert_awaited_once()
    call = trip_service.update_trip.await_args
    assert call.kwargs["trip_id"] == trip.id
    assert call.kwargs["user_id"] == principal.user.id
    update = call.kwargs["update"]
    assert isinstance(update, TripUpdateRequest)
    assert update.model_fields_set == {"title", "origin", "end_date"}
    assert update.title == "Updated London trip"
    assert update.origin is None


def test_update_trip_rejects_empty_payload_before_service_call() -> None:
    """An empty PATCH request should not produce a no-op database write."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.update_trip = AsyncMock()
    application = create_update_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.patch(f"/api/v1/trips/{uuid4()}", json={})

    assert response.status_code == 422
    trip_service.update_trip.assert_not_awaited()


def test_update_trip_rejects_null_required_field() -> None:
    """Required persisted trip fields must not be cleared through PATCH."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.update_trip = AsyncMock()
    application = create_update_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.patch(
            f"/api/v1/trips/{uuid4()}",
            json={"destination": None},
        )

    assert response.status_code == 422
    trip_service.update_trip.assert_not_awaited()


def test_update_trip_returns_safe_not_found_response() -> None:
    """A missing or differently owned trip should not reveal ownership."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.update_trip = AsyncMock(
        side_effect=TripNotFoundError("Trip was not found")
    )
    application = create_update_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.patch(
            f"/api/v1/trips/{uuid4()}",
            json={"title": "Updated title"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "trip_not_found"
    trip_service.update_trip.assert_awaited_once()
