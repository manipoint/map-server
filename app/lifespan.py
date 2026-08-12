"""Application resource lifecycle management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.database.session import create_database_engine, create_session_factory

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources."""
    settings: Settings = application.state.settings
    database_engine = create_database_engine(settings=settings)
    session_factory = create_session_factory(database_engine)
    application.state.database_engine = database_engine
    application.state.session_factory = session_factory

    logger.info(
        "Application started",
        extra={"app_env": settings.app_env},
    )
    try:
        yield
    finally:
        await database_engine.dispose()
        logger.info(
            "Application stopped",
            extra={"app_env": settings.app_env},
        )
