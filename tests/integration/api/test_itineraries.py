"""Integration tests for itinerary REST routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_principal, get_itinerary_service
from app.api.exception_handlers import (
    itinerary_exception_handler,
    trip_exception_handler,
)
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.itineraries import router
from app.auth.service import AuthenticatedPrincipal
from app.database.models import Itinerary, ItineraryItem, User
from app.database.repositories.itineraries import ItineraryDetails
from app.domain.errors import (
    InvalidItineraryStatusTransitionError,
    ItineraryError,
    ItineraryNotFoundError,
    TripError,
)
from app.services.itinerary_service import ItineraryService


def create_app(service: MagicMock, principal: MagicMock) -> FastAPI:
    """Create an isolated application containing itinerary routes."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(ItineraryError, itinerary_exception_handler)
    application.add_exception_handler(TripError, trip_exception_handler)
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_itinerary_service] = lambda: service
    application.dependency_overrides[get_current_principal] = lambda: principal
    return application


def create_principal() -> MagicMock:
    """Create one authenticated test principal."""

    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user = User(
        id=uuid4(),
        email="traveler@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    return principal


def create_details(*, status: str = "draft") -> ItineraryDetails:
    """Create one fully populated public itinerary result."""

    timestamp = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=2,
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
    )
    item = ItineraryItem(
        id=uuid4(),
        itinerary_id=itinerary.id,
        day_number=1,
        position=1,
        item_type="place",
        title="British Museum",
        description="Explore the galleries",
        location_name="London",
        starts_at=None,
        ends_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    return ItineraryDetails(itinerary=itinerary, items=[item])


def test_create_itinerary_returns_versioned_timeline() -> None:
    """Creating a draft should return its ordered Flutter-ready resource."""

    principal = create_principal()
    details = create_details()
    service = MagicMock(spec=ItineraryService)
    service.create_draft = AsyncMock(return_value=details)
    application = create_app(service, principal)

    with TestClient(application) as client:
        response = client.post(
            f"/api/v1/trips/{details.itinerary.trip_id}/itineraries",
            json={
                "items": [
                    {
                        "day_number": 1,
                        "position": 1,
                        "item_type": "place",
                        "title": "British Museum",
                    }
                ]
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(details.itinerary.id)
    assert body["version"] == 2
    assert body["status"] == "draft"
    assert body["items"][0]["title"] == "British Museum"
    assert "itinerary_id" not in body["items"][0]
    call = service.create_draft.await_args.kwargs
    assert call["trip_id"] == details.itinerary.trip_id
    assert call["user_id"] == principal.user.id
    assert call["items"][0].item_type == "place"


@pytest.mark.parametrize(
    ("path_template", "service_method"),
    [
        ("/api/v1/itineraries/{id}", "get_itinerary"),
        ("/api/v1/trips/{trip_id}/itinerary", "get_saved_itinerary"),
        ("/api/v1/itineraries/{id}/save", "save_itinerary"),
    ],
)
def test_read_and_save_routes_return_itinerary(
    path_template: str,
    service_method: str,
) -> None:
    """Detail, current-plan, and save routes should expose one representation."""

    principal = create_principal()
    details = create_details(status="saved")
    service = MagicMock(spec=ItineraryService)
    method = AsyncMock(return_value=details)
    setattr(service, service_method, method)
    application = create_app(service, principal)
    path = path_template.format(
        id=details.itinerary.id,
        trip_id=details.itinerary.trip_id,
    )

    with TestClient(application) as client:
        response = (
            client.post(path)
            if service_method == "save_itinerary"
            else client.get(path)
        )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    expected_id_name = (
        "trip_id" if service_method == "get_saved_itinerary" else "itinerary_id"
    )
    expected_id = (
        details.itinerary.trip_id
        if expected_id_name == "trip_id"
        else details.itinerary.id
    )
    method.assert_awaited_once_with(
        **{
            expected_id_name: expected_id,
            "user_id": principal.user.id,
        }
    )


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ItineraryNotFoundError("missing"), 404, "itinerary_not_found"),
        (
            InvalidItineraryStatusTransitionError("invalid"),
            409,
            "invalid_itinerary_status_transition",
        ),
    ],
)
def test_itinerary_routes_return_safe_domain_errors(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    """Internal domain messages should not leak through HTTP responses."""

    principal = create_principal()
    service = MagicMock(spec=ItineraryService)
    service.save_itinerary = AsyncMock(side_effect=error)
    application = create_app(service, principal)

    with TestClient(application) as client:
        response = client.post(f"/api/v1/itineraries/{uuid4()}/save")

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"] != str(error)


def test_invalid_itinerary_identifier_never_reaches_service() -> None:
    """Malformed route UUIDs should be rejected by FastAPI validation."""

    principal = create_principal()
    service = MagicMock(spec=ItineraryService)
    service.get_itinerary = AsyncMock()
    application = create_app(service, principal)

    with TestClient(application) as client:
        response = client.get("/api/v1/itineraries/not-a-uuid")

    assert response.status_code == 422
    service.get_itinerary.assert_not_awaited()
