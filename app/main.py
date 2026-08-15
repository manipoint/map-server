"""FastAPI application entry point."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exception_handlers import (
    authentication_exception_handler,
)
from app.api.middleware.access_log import AccessLogMiddleware
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.health import router as health_router
from app.auth.exceptions import AuthenticationError
from app.config import Settings, get_settings
from app.lifespan import lifespan
from app.observability.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
        ],
        expose_headers=[
            "X-Request-ID",
        ],
        max_age=600,
    )
    application.add_middleware(AccessLogMiddleware)
    application.add_middleware(RequestIdMiddleware)

    application.include_router(health_router)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    logger.info(
        "Application configured",
        extra={
            "app_env": resolved_settings.app_env,
            "debug": resolved_settings.debug,
        },
    )
    return application


app = create_app()
