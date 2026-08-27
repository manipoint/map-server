"""Integration tests for the plan-trip endpoint."""

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
from app.domain.errors import (
    InvalidTripStatusTransitionError,
    TripError,
    TripNotFoundError,
)
from app.services.trip_service import TripService


def create_plan_app(
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


def create_planned_trip(*, user_id: object) -> Trip:
    """Create a planned trip returned by the service."""

    timestamp = datetime(2026, 8, 27, 12, 30, tzinfo=UTC)
    return Trip(
        id=uuid4(),
        user_id=user_id,
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
        status="planned",
        created_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        updated_at=timestamp,
    )


def test_plan_trip_returns_planned_trip() -> None:
    """A draft trip should be transitioned for its authenticated owner."""

    principal = create_principal()
    trip = create_planned_trip(user_id=principal.user.id)
    trip_service = MagicMock(spec=TripService)
    trip_service.mark_trip_planned = AsyncMock(return_value=trip)
    application = create_plan_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.post(f"/api/v1/trips/{trip.id}/plan")

    assert response.status_code == 200
    assert response.json()["id"] == str(trip.id)
    assert response.json()["status"] == "planned"
    assert "user_id" not in response.json()
    trip_service.mark_trip_planned.assert_awaited_once_with(
        trip_id=trip.id,
        user_id=principal.user.id,
    )


def test_plan_trip_allows_idempotent_service_result() -> None:
    """Repeating the command should return the already-planned trip."""

    principal = create_principal()
    trip = create_planned_trip(user_id=principal.user.id)
    trip_service = MagicMock(spec=TripService)
    trip_service.mark_trip_planned = AsyncMock(return_value=trip)
    application = create_plan_app(trip_service, principal)

    with TestClient(application) as client:
        first_response = client.post(f"/api/v1/trips/{trip.id}/plan")
        second_response = client.post(f"/api/v1/trips/{trip.id}/plan")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json() == second_response.json()
    assert trip_service.mark_trip_planned.await_count == 2


def test_plan_trip_returns_conflict_for_invalid_transition() -> None:
    """An archived trip must not transition back to planned."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.mark_trip_planned = AsyncMock(
        side_effect=InvalidTripStatusTransitionError(
            "Trip cannot transition from archived to planned"
        )
    )
    application = create_plan_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.post(f"/api/v1/trips/{uuid4()}/plan")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_trip_status_transition"
    assert "archived" not in response.text


def test_plan_trip_returns_safe_not_found_response() -> None:
    """A missing or differently owned trip should not reveal ownership."""

    principal = create_principal()
    trip_service = MagicMock(spec=TripService)
    trip_service.mark_trip_planned = AsyncMock(
        side_effect=TripNotFoundError("Trip was not found")
    )
    application = create_plan_app(trip_service, principal)

    with TestClient(application) as client:
        response = client.post(f"/api/v1/trips/{uuid4()}/plan")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "trip_not_found"
