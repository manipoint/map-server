"""Application resource lifecycle management."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from google.cloud.sql.connector import Connector

from app.api.websocket.connection_manager import ConnectionManager
from app.common.exceptions import ProviderConfigurationError
from app.config import Settings
from app.database.session import (
    create_cloud_sql_resources,
    create_database_engine,
    create_session_factory,
)
from app.graph.builder import build_travel_graph
from app.graph.subgraphs.model_gateway import build_model_gateway
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.observability.langsmith import create_langsmith_tracer_factory
from app.providers.airports.client import AirportProvider
from app.providers.airports.local_provider import LocalAirportProvider
from app.providers.currency.frankfurter_client import FrankfurterCurrencyClient
from app.providers.flights.client import FlightProvider
from app.providers.flights.local_metadata_provider import LocalFlightMetadataProvider
from app.providers.hotels.client import HotelProvider
from app.providers.locations.weatherapi_client import WeatherApiLocationClient
from app.providers.places.google_client import GooglePlacesClient
from app.providers.serpapi.deals_client import SerpApiDealsClient
from app.providers.serpapi.flight_client import SerpApiFlightClient
from app.providers.serpapi.hotel_client import SerpApiHotelClient
from app.providers.weather.client import WeatherApiClient
from app.services.airport_resolution_service import AirportResolutionService
from app.services.deal_discovery_service import DealDiscoveryService
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationService,
)
from app.services.flight_search_service import FlightSearchService
from app.services.hotel_search_service import HotelSearchService
from app.services.location_resolution_service import LocationResolutionService
from app.services.place_search_service import PlaceSearchService
from app.services.planning_research_service import PlanningResearchService
from app.services.standalone_search_service import StandaloneSearchService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources."""
    settings: Settings = application.state.settings
    cloud_sql_connector: Connector | None = None
    connection_manager: ConnectionManager | None = None
    http_client: httpx.AsyncClient | None = None
    flight_provider: FlightProvider | None = None
    airport_provider: AirportProvider | None = None
    hotel_provider: HotelProvider | None = None
    location_provider: WeatherApiLocationClient | None = None
    place_provider: GooglePlacesClient | None = None
    currency_provider: FrankfurterCurrencyClient | None = None
    langsmith_tracer_factory = create_langsmith_tracer_factory(settings)
    hotel_search_service: HotelSearchService | None = None
    place_search_service: PlaceSearchService | None = None
    location_resolution_service: LocationResolutionService | None = None
    flight_search_service: FlightSearchService | None = None
    flight_search_preparation_service: FlightSearchPreparationService | None = None
    airport_resolution_service: AirportResolutionService | None = None
    deal_discovery_service: DealDiscoveryService | None = None

    if settings.database_connection_mode == "cloud_sql":
        database_engine, cloud_sql_connector = await create_cloud_sql_resources(
            settings=settings
        )
    else:
        database_engine = create_database_engine(settings=settings)

    try:
        session_factory = create_session_factory(database_engine)
        connection_manager = ConnectionManager()
        http_client = httpx.AsyncClient()
        weather_provider = WeatherApiClient(
            http_client=http_client,
            settings=settings,
        )
        if settings.currency_provider == "frankfurter":
            currency_provider = FrankfurterCurrencyClient(
                http_client=http_client,
                settings=settings,
            )
        if settings.places_provider == "google":
            location_provider = WeatherApiLocationClient(
                http_client=http_client,
                settings=settings,
            )
            place_provider = GooglePlacesClient(
                http_client=http_client,
                settings=settings,
            )
            place_search_service = PlaceSearchService(
                location_provider=location_provider,
                place_provider=place_provider,
            )
            location_resolution_service = LocationResolutionService(
                provider=place_provider,
            )
        if settings.flight_provider == "serpapi":
            metadata_path = settings.flight_metadata_path
            directory_path = settings.airport_directory_path
            if metadata_path is None or not metadata_path.strip():
                raise ProviderConfigurationError("Flight metadata path is required")
            if directory_path is None or not directory_path.strip():
                raise ProviderConfigurationError("Airport directory path is required")
            flight_metadata_provider = await LocalFlightMetadataProvider.from_file(
                path=Path(metadata_path)
            )
            airport_provider = await LocalAirportProvider.from_file(
                path=Path(directory_path),
            )
            if not airport_provider.airport_codes.issubset(
                flight_metadata_provider.airport_codes
            ):
                raise ProviderConfigurationError(
                    "Airport directory contains airports without timezone metadata"
                )
            flight_provider = SerpApiFlightClient(
                http_client=http_client,
                metadata_provider=flight_metadata_provider,
                settings=settings,
            )
            flight_search_service = FlightSearchService(
                flight_provider=flight_provider,
                self_service_traveler_limit=9,
                supports_round_trip=True,
            )
            airport_resolution_service = AirportResolutionService(
                airport_provider=airport_provider,
            )

            flight_search_preparation_service = FlightSearchPreparationService(
                airport_resolution_service=airport_resolution_service,
                flight_search_service=flight_search_service,
            )
            deal_discovery_service = DealDiscoveryService(
                airport_resolution_service=airport_resolution_service,
                deals_client=SerpApiDealsClient(
                    http_client=http_client, settings=settings
                ),
            )
        if settings.hotel_provider == "serpapi":
            if location_provider is None:
                location_provider = WeatherApiLocationClient(
                    http_client=http_client,
                    settings=settings,
                )
            hotel_provider = SerpApiHotelClient(
                http_client=http_client,
                settings=settings,
            )
            hotel_search_service = HotelSearchService(
                location_provider=location_provider,
                hotel_provider=hotel_provider,
                radius_km=25,
            )
        mcp_server = create_mcp_server(
            weather_provider=weather_provider,
            airport_resolution_service=airport_resolution_service,
            flight_search_service=flight_search_preparation_service,
            hotel_search_service=hotel_search_service,
            place_search_service=place_search_service,
            currency_provider=currency_provider,
        )
        mcp_client = TravelMcpClient(mcp_server=mcp_server)

        model_gateway = build_model_gateway(settings=settings)
        travel_graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=(),
            max_tool_rounds=settings.max_tool_rounds,
            standalone_service=StandaloneSearchService(
                client=mcp_client,
                enabled=frozenset(
                    {"weather"}
                    | ({"currency"} if currency_provider else set())
                    | ({"places"} if place_search_service else set())
                    | ({"flight_deals"} if deal_discovery_service else set())
                    | ({"flights"} if flight_search_service else set())
                    | ({"hotels"} if hotel_search_service else set())
                ),
                timeout_seconds=settings.provider_timeout_seconds,
                deal_discovery_service=deal_discovery_service,
            ),
            research_service=PlanningResearchService(
                client=mcp_client,
                session_factory=session_factory,
                places_available=place_search_service is not None,
                hotels_available=hotel_search_service is not None,
                round_trip_flights_available=(
                    flight_search_service is not None
                    and flight_search_service.supports_round_trip
                ),
                weather_forecasts_available=True,
            ),
            deal_discovery_service=deal_discovery_service,
        )

        application.state.database_engine = database_engine
        application.state.session_factory = session_factory
        application.state.connection_manager = connection_manager
        application.state.travel_graph = travel_graph
        application.state.planning_graph_enabled = True
        application.state.http_client = http_client
        application.state.weather_provider = weather_provider
        application.state.airport_provider = airport_provider
        application.state.airport_resolution_service = airport_resolution_service
        application.state.flight_provider = flight_provider
        application.state.flight_search_service = flight_search_service
        application.state.flight_search_preparation_service = (
            flight_search_preparation_service
        )
        application.state.location_provider = location_provider
        application.state.hotel_provider = hotel_provider
        application.state.hotel_search_service = hotel_search_service
        application.state.place_provider = place_provider
        application.state.place_search_service = place_search_service
        application.state.location_resolution_service = location_resolution_service
        application.state.currency_provider = currency_provider
        application.state.mcp_server = mcp_server
        application.state.mcp_client = mcp_client
        application.state.langsmith_tracer_factory = langsmith_tracer_factory

        logger.info(
            "Application started",
            extra={
                "app_env": settings.app_env,
                "langsmith_tracing_enabled": langsmith_tracer_factory is not None,
            },
        )
        yield
    finally:
        try:
            if langsmith_tracer_factory is not None:
                try:
                    await asyncio.to_thread(
                        langsmith_tracer_factory.client.flush, timeout=2.0
                    )
                except Exception:
                    logger.warning("LangSmith trace flush failed during shutdown")
        finally:
            try:
                if connection_manager is not None:
                    closed_connection_count = await connection_manager.close_all()
                    logger.info(
                        "WebSocket connections closed",
                        extra={"connection_count": closed_connection_count},
                    )
            finally:
                try:
                    if http_client is not None:
                        await http_client.aclose()
                finally:
                    try:
                        await database_engine.dispose()
                    finally:
                        if cloud_sql_connector is not None:
                            await cloud_sql_connector.close_async()
        logger.info(
            "Application stopped",
            extra={"app_env": settings.app_env},
        )
