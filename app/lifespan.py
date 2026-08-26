"""Application resource lifecycle management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from google.cloud.sql.connector import Connector

from app.api.websocket.connection_manager import ConnectionManager
from app.config import Settings
from app.database.session import (
    create_cloud_sql_resources,
    create_database_engine,
    create_session_factory,
)
from app.graph.builder import build_travel_graph
from app.graph.subgraphs.model_gateway import build_model_gateway
from app.graph.tools import (
    create_currency_conversion_tool,
    create_current_weather_tool,
    create_flight_search_tool,
    create_hotel_search_tool,
    create_place_search_tool,
)
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.providers.currency.frankfurter_client import FrankfurterCurrencyClient
from app.providers.flights.duffel_client import DuffelFlightClient
from app.providers.hotels.duffel_client import DuffelHotelClient
from app.providers.locations.weatherapi_client import WeatherApiLocationClient
from app.providers.places.google_client import GooglePlacesClient
from app.providers.weather.client import WeatherApiClient
from app.services.flight_search_service import FlightSearchService
from app.services.hotel_search_service import HotelSearchService
from app.services.place_search_service import PlaceSearchService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources."""
    settings: Settings = application.state.settings
    cloud_sql_connector: Connector | None = None
    connection_manager: ConnectionManager | None = None
    http_client: httpx.AsyncClient | None = None
    flight_provider: DuffelFlightClient | None = None
    hotel_provider: DuffelHotelClient | None = None
    location_provider: WeatherApiLocationClient | None = None
    place_provider: GooglePlacesClient | None = None
    currency_provider: FrankfurterCurrencyClient | None = None
    hotel_search_service: HotelSearchService | None = None
    place_search_service: PlaceSearchService | None = None
    flight_search_service: FlightSearchService | None = None

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
        if settings.flight_provider == "duffel":
            flight_provider = DuffelFlightClient(
                http_client=http_client,
                settings=settings,
            )
            flight_search_service = FlightSearchService(
                flight_provider=flight_provider,
            )
        if settings.hotel_provider == "duffel" or settings.places_provider == "google":
            location_provider = WeatherApiLocationClient(
                http_client=http_client,
                settings=settings,
            )
        if settings.hotel_provider == "duffel":
            assert location_provider is not None

            hotel_provider = DuffelHotelClient(
                http_client=http_client,
                settings=settings,
            )
            hotel_search_service = HotelSearchService(
                location_provider=location_provider,
                hotel_provider=hotel_provider,
                radius_km=settings.duffel_stays_radius_km,
            )

        if settings.places_provider == "google":
            assert location_provider is not None
            place_provider = GooglePlacesClient(
                http_client=http_client,
                settings=settings,
            )
            place_search_service = PlaceSearchService(
                location_provider=location_provider,
                place_provider=place_provider,
            )

        mcp_server = create_mcp_server(
            weather_provider=weather_provider,
            flight_provider=flight_search_service,
            hotel_search_service=hotel_search_service,
            place_search_service=place_search_service,
            currency_provider=currency_provider,
        )
        mcp_client = TravelMcpClient(mcp_server=mcp_server)

        tools = [
            create_current_weather_tool(
                mcp_client=mcp_client,
            ),
        ]
        if flight_search_service is not None:
            tools.append(create_flight_search_tool(mcp_client=mcp_client))
        if hotel_search_service is not None:
            tools.append(create_hotel_search_tool(mcp_client=mcp_client))
        if place_search_service is not None:
            tools.append(
                create_place_search_tool(
                    mcp_client=mcp_client,
                )
            )
        if currency_provider is not None:
            tools.append(create_currency_conversion_tool(mcp_client=mcp_client))
        model_gateway = build_model_gateway(settings=settings, tools=tools)
        travel_graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=tools,
            max_tool_rounds=settings.max_tool_rounds,
        )

        application.state.database_engine = database_engine
        application.state.session_factory = session_factory
        application.state.connection_manager = connection_manager
        application.state.travel_graph = travel_graph
        application.state.http_client = http_client
        application.state.weather_provider = weather_provider
        application.state.flight_provider = flight_provider
        application.state.flight_search_service = flight_search_service
        application.state.location_provider = location_provider
        application.state.hotel_provider = hotel_provider
        application.state.hotel_search_service = hotel_search_service
        application.state.place_provider = place_provider
        application.state.place_search_service = place_search_service
        application.state.currency_provider = currency_provider
        application.state.mcp_server = mcp_server
        application.state.mcp_client = mcp_client

        logger.info(
            "Application started",
            extra={"app_env": settings.app_env},
        )
        yield
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
