"""Health-check routes."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """Health-check response."""

    status: Literal["ok"] = "ok"


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
