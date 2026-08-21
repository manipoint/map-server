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
from app.graph.tools import create_current_weather_tool
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.providers.weather.client import WeatherApiClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources."""
    settings: Settings = application.state.settings
    cloud_sql_connector: Connector | None = None
    connection_manager: ConnectionManager | None = None
    http_client: httpx.AsyncClient | None = None

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
        mcp_server = create_mcp_server(weather_provider=weather_provider)
        mcp_client = TravelMcpClient(mcp_server=mcp_server)
        weather_tool = create_current_weather_tool(mcp_client=mcp_client)
        model_gateway = build_model_gateway(settings=settings, tools=[weather_tool])
        travel_graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=[weather_tool],
            max_tool_rounds=settings.max_tool_rounds,
        )

        application.state.database_engine = database_engine
        application.state.session_factory = session_factory
        application.state.connection_manager = connection_manager
        application.state.travel_graph = travel_graph
        application.state.http_client = http_client
        application.state.weather_provider = weather_provider
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
