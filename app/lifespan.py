"""Application resource lifecycle management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources."""
    settings: Settings = application.state.settings
    logger.info(
        "Application started",
        extra={"app_env": settings.app_env},
    )
    try:
        yield
    finally:
        logger.info(
            "Application stopped",
            extra={"app_env": settings.app_env},
        )
