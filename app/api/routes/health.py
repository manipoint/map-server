"""Health-check routes."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.dependencies import DatabaseEngineDependency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """Health-check response."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Database readiness response."""

    status: Literal["ready", "not_ready"]


@router.get(
    "/live",
    response_model=HealthResponse,
    summary="Check whether the API process is responsive",
)
async def liveness_check() -> HealthResponse:
    """
    Return process liveness without checking external services.
    """
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "Required database dependency is unavailable",
        }
    },
    summary="Check whether required dependencies are ready",
)
async def readiness_check(
    request: Request,
    database_engine: DatabaseEngineDependency,
) -> ReadinessResponse | JSONResponse:
    """Return whether the required database dependency is available."""

    settings = request.app.state.settings
    try:
        async with asyncio.timeout(settings.database_readiness_timeout_seconds):
            async with database_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning(
            "Database readiness check failed",
            exc_info=True,
        )
        response = ReadinessResponse(status="not_ready")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(mode="json"),
        )

    return ReadinessResponse(status="ready")
