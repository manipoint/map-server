"""Integration tests for authenticated destination catalogue routes."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_current_principal,
    get_destination_catalogue_service,
)
from app.api.exception_handlers import (
    authentication_exception_handler,
    destination_exception_handler,
    invalid_cursor_exception_handler,
)
from app.api.routes.destinations import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal
from app.common.exceptions import InvalidCursorError
from app.database.models.user import User
from app.domain.destinations import (
    DestinationCandidate,
    DestinationCollection,
    DestinationDetail,
    DestinationType,
    MediaAssetValue,
)
from app.domain.errors import (
    DestinationError,
    DestinationNotFoundError,
    DestinationPlaceNotFoundError,
)
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle
from app.services.destination_catalogue_service import (
    DestinationCatalogueService,
    DestinationPage,
)


def candidate() -> DestinationCandidate:
    """Create one complete public destination candidate."""

    cover = MediaAssetValue(
        id=uuid4(),
        url="https://images.example.com/skardu.jpg",
        alt_text="Lake and mountains near Skardu",
        caption=None,
        width=None,
        height=None,
    )
    return DestinationCandidate(
        id=uuid4(),
        slug="skardu-pakistan",
        name="Skardu",
        destination_type=DestinationType.REGION,
        country_name="Pakistan",
        country_code="PK",
        summary="A sufficiently descriptive destination summary for testing.",
        full_description="A complete destination description for catalogue tests.",
        cover_image=cover,
        latitude=35.2971,
        longitude=75.6333,
        map_zoom=9,
        budget_tier=BudgetTier.MID_RANGE,
        styles=(TravelStyle.ADVENTURE, TravelStyle.NATURE),
        interests=(TravelInterest.HIKING,),
        editorial_rank=1,
        featured_rank=1,
        popular_rank=1,
    )


def create_app() -> tuple[FastAPI, MagicMock]:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    service = MagicMock(spec=DestinationCatalogueService)
    service.list_destinations = AsyncMock(
        return_value=DestinationPage(items=(candidate(),), next_cursor=None)
    )
    service.get_destination = AsyncMock(
        return_value=DestinationDetail(
            destination=candidate(),
            gallery=(),
            places=(),
        )
    )
    application.dependency_overrides[get_destination_catalogue_service] = lambda: (
        service
    )
    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user = User(
        id=uuid4(),
        email="traveler@example.com",
        password_hash="stored-hash",
        status="active",
    )
    application.dependency_overrides[get_current_principal] = lambda: principal
    return application, service


def test_view_all_serializes_cover_and_collection_parameters() -> None:
    """Flutter should receive cards while the backend owns ranking."""

    application, service = create_app()
    with TestClient(application) as client:
        response = client.get("/api/v1/destinations?collection=popular&limit=10")

    assert response.status_code == 200
    assert response.json()["items"][0]["cover_image"]["alt_text"].startswith("Lake")
    assert service.list_destinations.await_args.kwargs["collection"] is (
        DestinationCollection.POPULAR
    )
    assert service.list_destinations.await_args.kwargs["limit"] == 10


def test_destination_detail_contains_map_and_gallery_contract() -> None:
    """Destination detail should expose a map centre and bounded media list."""

    application, _ = create_app()
    with TestClient(application) as client:
        response = client.get("/api/v1/destinations/skardu-pakistan")

    assert response.status_code == 200
    assert response.json()["location"] == {
        "latitude": 35.2971,
        "longitude": 75.6333,
        "map_zoom": 9,
    }
    assert response.json()["gallery"] == []
    assert response.json()["has_more_places"] is False
    assert response.json()["places_next_cursor"] is None


def test_invalid_cursor_returns_safe_422() -> None:
    application, service = create_app()
    application.add_exception_handler(
        InvalidCursorError, invalid_cursor_exception_handler
    )

    async def validate_cursor(**kwargs):
        DestinationCatalogueService._decode_cursor(cursor=kwargs["cursor"])

    service.list_destinations.side_effect = validate_cursor
    cursor = base64.urlsafe_b64encode(
        json.dumps({"v": 2, "context": "x", "key": [0, 1, 123]}).encode()
    ).decode()
    with TestClient(application) as client:
        response = client.get("/api/v1/destinations", params={"cursor": cursor})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


def test_nested_missing_place_has_safe_404() -> None:
    application, service = create_app()
    application.add_exception_handler(DestinationError, destination_exception_handler)
    service.get_place = AsyncMock(
        side_effect=DestinationPlaceNotFoundError("internal detail")
    )
    with TestClient(application) as client:
        response = client.get("/api/v1/destinations/skardu/places/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "destination_place_not_found"
    assert "internal detail" not in response.text


def test_unpublished_destination_has_safe_404() -> None:
    application, service = create_app()
    application.add_exception_handler(DestinationError, destination_exception_handler)
    service.get_destination.side_effect = DestinationNotFoundError("internal detail")
    with TestClient(application) as client:
        response = client.get("/api/v1/destinations/unpublished")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "destination_not_found"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/destinations",
        "/api/v1/destinations/skardu",
        "/api/v1/destinations/skardu/places",
        "/api/v1/destinations/skardu/places/lake",
    ],
)
def test_catalogue_requires_authentication(path: str) -> None:
    application, service = create_app()
    del application.dependency_overrides[get_current_principal]
    application.dependency_overrides[get_auth_service] = lambda: MagicMock()
    application.add_exception_handler(
        AuthenticationError, authentication_exception_handler
    )
    with TestClient(application) as client:
        response = client.get(path)
    assert response.status_code == 401
    service.list_destinations.assert_not_awaited()
    service.get_destination.assert_not_awaited()
