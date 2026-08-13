"""Application resource lifecycle management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from google.cloud.sql.connector import Connector

from app.config import Settings
from app.database.session import (
    create_cloud_sql_resources,
    create_database_engine,
    create_session_factory,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources."""
    settings: Settings = application.state.settings
    cloud_sql_connector: Connector | None = None

    if settings.database_connection_mode == "cloud_sql":
        database_engine, cloud_sql_connector = await create_cloud_sql_resources(
            settings=settings
        )
    else:
        database_engine = create_database_engine(settings=settings)

    try:
        session_factory = create_session_factory(database_engine)
        application.state.database_engine = database_engine
        application.state.session_factory = session_factory

        logger.info(
            "Application started",
            extra={"app_env": settings.app_env},
        )
        yield
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
