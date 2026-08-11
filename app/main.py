"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    resolved_settings = settings or get_settings()
    application = FastAPI(
        title=resolved_settings.app_name, version="0.1.0", debug=resolved_settings.debug
    )
    application.state.settings = resolved_settings
    application.include_router(health_router)
    return application


app = create_app()
