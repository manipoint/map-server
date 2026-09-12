"""Integration tests for authenticated Home discovery."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_current_principal,
    get_home_discovery_service,
)
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.home import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.database.models.user import User
from app.domain.destinations import (
    DestinationCandidate,
    DestinationType,
    DiscoveryCollectionKind,
    HomeDiscovery,
    MediaAssetValue,
)
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle
from app.services.home_discovery_service import HomeDiscoveryService


def create_discovery() -> HomeDiscovery:
    """Create one complete service result for route serialization."""

    destination = DestinationCandidate(
        id=uuid4(),
        slug="hunza-pakistan",
        name="Hunza",
        destination_type=DestinationType.REGION,
        country_name="Pakistan",
        country_code="PK",
        summary="A mountain destination with trails and expansive valley views.",
        full_description="A complete mountain destination description for testing.",
        cover_image=MediaAssetValue(
            id=uuid4(),
            url="https://images.example.com/hunza.jpg",
            alt_text="Hunza valley",
            caption=None,
            width=None,
            height=None,
        ),
        latitude=36.3167,
        longitude=74.65,
        map_zoom=9,
        budget_tier=BudgetTier.MID_RANGE,
        styles=(TravelStyle.NATURE,),
        interests=(TravelInterest.HIKING,),
        editorial_rank=1,
        featured_rank=1,
        popular_rank=1,
    )
    return HomeDiscovery(
        personalization_ready=True,
        suggested=(destination,),
        suggested_local=(destination,),
        suggested_international=(),
        popular=(destination,),
        spotlight_kind=DiscoveryCollectionKind.FEATURED,
        spotlight=(destination,),
    )


def create_app(*, authenticated: bool = True) -> tuple[FastAPI, MagicMock]:
    """Create an isolated Home API and mocked service."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    service = MagicMock(spec=HomeDiscoveryService)
    service.get_home = AsyncMock(return_value=create_discovery())
    application.dependency_overrides[get_home_discovery_service] = lambda: service

    if authenticated:
        principal = MagicMock(spec=AuthenticatedPrincipal)
        principal.user = User(
            id=uuid4(),
            email="traveler@example.com",
            password_hash="stored-hash",
            status="active",
        )
        application.dependency_overrides[get_current_principal] = lambda: principal
    else:
        auth_service = MagicMock(spec=AuthService)
        application.dependency_overrides[get_auth_service] = lambda: auth_service
    return application, service


def test_home_returns_single_cacheable_flutter_payload() -> None:
    """One request should return every low-cost Home discovery section."""

    application, service = create_app()

    with TestClient(application) as client:
        response = client.get("/api/v1/home?limit=4")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.json()["personalization_ready"] is True
    assert response.json()["suggested"][0]["slug"] == "hunza-pakistan"
    assert response.json()["popular"][0]["slug"] == "hunza-pakistan"
    assert response.json()["spotlight"]["kind"] == "featured"
    service.get_home.assert_awaited_once()
    assert service.get_home.await_args.kwargs["section_limit"] == 4


def test_home_rejects_limit_above_cost_boundary() -> None:
    """Validation should stop oversized requests before catalogue access."""

    application, service = create_app()

    with TestClient(application) as client:
        response = client.get("/api/v1/home?limit=7")

    assert response.status_code == 422
    service.get_home.assert_not_awaited()


def test_home_requires_authentication() -> None:
    """Personalized discovery must not be exposed without a valid session."""

    application, service = create_app(authenticated=False)

    with TestClient(application) as client:
        response = client.get("/api/v1/home")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"
    service.get_home.assert_not_awaited()
