"""FastAPI dependency providers."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.websocket.connection_manager import ConnectionManager
from app.auth.exceptions import InvalidAccessTokenError
from app.auth.service import (
    AuthenticatedPrincipal,
    AuthService,
)
from app.common.exceptions import ProviderConfigurationError
from app.config import Settings
from app.database.session import AsyncSessionFactory
from app.services.conversation_processing_service import (
    ConversationProcessingService,
)
from app.services.destination_catalogue_service import DestinationCatalogueService
from app.services.home_discovery_service import HomeDiscoveryService
from app.services.itinerary_service import ItineraryService
from app.services.location_resolution_service import LocationResolutionService
from app.services.travel_response_service import TravelResponseService
from app.services.trip_service import TripService
from app.services.user_preference_service import UserPreferenceService


async def get_database_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Provide one database session for one HTTP request."""
    session_factory: AsyncSessionFactory = request.app.state.session_factory
    session = session_factory()

    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_database_session),
]
bearer_scheme = HTTPBearer(
    auto_error=False,
)


def get_auth_service(
    request: Request,
    database_session: DatabaseSession,
) -> AuthService:
    """Create an authentication service for one HTTP request."""

    settings: Settings = request.app.state.settings

    return AuthService(
        session=database_session,
        settings=settings,
    )


AuthServiceDependency = Annotated[
    AuthService,
    Depends(get_auth_service),
]


def get_trip_service(
    database_session: DatabaseSession,
) -> TripService:
    """Create one database-bound trip service per HTTP request."""

    return TripService(session=database_session)


TripServiceDependency = Annotated[
    TripService,
    Depends(get_trip_service),
]


def get_user_preference_service(
    database_session: DatabaseSession,
) -> UserPreferenceService:
    """Create one database-bound preference service per HTTP request."""

    return UserPreferenceService(session=database_session)


UserPreferenceServiceDependency = Annotated[
    UserPreferenceService,
    Depends(get_user_preference_service),
]


def get_home_discovery_service(
    database_session: DatabaseSession,
) -> HomeDiscoveryService:
    """Create one database-only Home discovery service per request."""

    return HomeDiscoveryService(session=database_session)


HomeDiscoveryServiceDependency = Annotated[
    HomeDiscoveryService,
    Depends(get_home_discovery_service),
]


def get_destination_catalogue_service(
    database_session: DatabaseSession,
) -> DestinationCatalogueService:
    """Create one database-only destination catalogue service per request."""

    return DestinationCatalogueService(session=database_session)


DestinationCatalogueServiceDependency = Annotated[
    DestinationCatalogueService,
    Depends(get_destination_catalogue_service),
]


def get_itinerary_service(
    database_session: DatabaseSession,
) -> ItineraryService:
    """Create one database-bound itinerary service per HTTP request."""

    return ItineraryService(session=database_session)


ItineraryServiceDependency = Annotated[
    ItineraryService,
    Depends(get_itinerary_service),
]


def get_location_resolution_service(request: Request) -> LocationResolutionService:
    """Return the startup-owned canonical location service."""

    service: LocationResolutionService | None = getattr(
        request.app.state,
        "location_resolution_service",
        None,
    )
    if service is None:
        raise ProviderConfigurationError("Location resolution is not configured")
    return service


LocationResolutionServiceDependency = Annotated[
    LocationResolutionService,
    Depends(get_location_resolution_service),
]


BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]


async def get_current_principal(
    credentials: BearerCredentials,
    auth_service: AuthServiceDependency,
) -> AuthenticatedPrincipal:
    """Authenticate the request bearer token."""

    if credentials is None:
        raise InvalidAccessTokenError("Access token is required")

    if credentials.scheme.lower() != "bearer":
        raise InvalidAccessTokenError("Bearer access token is required")

    return await auth_service.authenticate_access_token(
        access_token=credentials.credentials,
    )


CurrentPrincipal = Annotated[
    AuthenticatedPrincipal,
    Depends(get_current_principal),
]


def get_connection_manager(request: Request) -> ConnectionManager:
    """Return the application WebSocket connection manager."""
    return request.app.state.connection_manager


ConnectionManagerDependency = Annotated[
    ConnectionManager, Depends(get_connection_manager)
]


async def get_travel_response_service(
    request: Request,
    database_session: DatabaseSession,
) -> TravelResponseService:
    """Create one database-bound travel response service."""
    settings: Settings = request.app.state.settings
    graph = request.app.state.travel_graph
    processing_service = ConversationProcessingService(
        session=database_session,
        history_limit=settings.conversation_history_message_limit,
    )
    return TravelResponseService(
        processing_service=processing_service,
        itinerary_service=ItineraryService(session=database_session),
        graph=graph,
        assistant_run_lease_seconds=settings.assistant_run_lease_seconds,
        travel_response_timeout_seconds=settings.travel_response_timeout_seconds,
        max_model_attempts=settings.max_model_attempts,
    )


TravelResponseServiceDependency = Annotated[
    TravelResponseService,
    Depends(get_travel_response_service),
]


def get_database_engine(request: Request) -> AsyncEngine:
    """Return the database engine owned by application lifespan."""
    return request.app.state.database_engine


DatabaseEngineDependency = Annotated[AsyncEngine, Depends(get_database_engine)]
