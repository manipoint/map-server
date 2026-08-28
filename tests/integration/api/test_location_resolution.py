"""Integration tests for authenticated canonical location resolution."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_current_principal,
    get_location_resolution_service,
)
from app.api.exception_handlers import (
    authentication_exception_handler,
    provider_exception_handler,
)
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.locations import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.common.exceptions import ProviderError, ProviderUnavailableError
from app.domain.trips import CanonicalLocation
from app.services.location_resolution_service import LocationResolutionService


def create_app(
    service: MagicMock,
    *,
    authenticated: bool = True,
) -> FastAPI:
    """Create one isolated application containing the location route."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.add_exception_handler(ProviderError, provider_exception_handler)
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_location_resolution_service] = lambda: service
    if authenticated:
        principal = MagicMock(spec=AuthenticatedPrincipal)
        application.dependency_overrides[get_current_principal] = lambda: principal
    else:
        auth_service = MagicMock(spec=AuthService)
        application.dependency_overrides[get_auth_service] = lambda: auth_service
    return application


def test_resolve_location_returns_canonical_options() -> None:
    """An authenticated caller should receive Flutter-ready location objects."""

    option = CanonicalLocation(
        provider="google",
        provider_location_id="london-id",
        canonical_name="London, United Kingdom",
        country_code="GB",
        latitude=51.5074,
        longitude=-0.1278,
    )
    service = MagicMock(spec=LocationResolutionService)
    service.resolve = AsyncMock(return_value=[option])
    application = create_app(service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/locations/resolve",
            params={"query": "  London  ", "limit": 3},
        )

    assert response.status_code == 200
    assert response.json() == {
        "query": "London",
        "options": [
            {
                "provider": "google",
                "provider_location_id": "london-id",
                "canonical_name": "London, United Kingdom",
                "country_code": "GB",
                "latitude": 51.5074,
                "longitude": -0.1278,
            }
        ],
    }
    service.resolve.assert_awaited_once_with(query="London", max_results=3)


def test_resolve_location_rejects_unbounded_limit_before_service() -> None:
    """HTTP validation should prevent requests beyond the provider cost cap."""

    service = MagicMock(spec=LocationResolutionService)
    service.resolve = AsyncMock()
    application = create_app(service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/locations/resolve",
            params={"query": "London", "limit": 6},
        )

    assert response.status_code == 422
    service.resolve.assert_not_awaited()


def test_resolve_location_rejects_whitespace_query_before_service() -> None:
    """Whitespace must not escape HTTP validation and become an internal error."""

    service = MagicMock(spec=LocationResolutionService)
    service.resolve = AsyncMock()
    application = create_app(service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/locations/resolve",
            params={"query": "   "},
        )

    assert response.status_code == 422
    service.resolve.assert_not_awaited()


def test_resolve_location_requires_authentication() -> None:
    """Location provider quota must not be exposed anonymously."""

    service = MagicMock(spec=LocationResolutionService)
    service.resolve = AsyncMock()
    application = create_app(service, authenticated=False)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/locations/resolve",
            params={"query": "London"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"
    service.resolve.assert_not_awaited()


def test_resolve_location_returns_safe_provider_error() -> None:
    """Provider failures should not expose credentials or transport details."""

    service = MagicMock(spec=LocationResolutionService)
    service.resolve = AsyncMock(
        side_effect=ProviderUnavailableError("private upstream detail")
    )
    application = create_app(service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/locations/resolve",
            params={"query": "London"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert response.json()["error"]["message"] == (
        "External provider is temporarily unavailable"
    )
    assert "private upstream detail" not in response.text
