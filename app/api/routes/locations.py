"""Authenticated canonical location-resolution routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentPrincipal, LocationResolutionServiceDependency
from app.api.schemas.locations import LocationQuery, LocationResolutionResponse

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get(
    "/resolve",
    response_model=LocationResolutionResponse,
    summary="Resolve a location for explicit user selection",
)
async def resolve_location(
    principal: CurrentPrincipal,
    service: LocationResolutionServiceDependency,
    query: Annotated[LocationQuery, Query()],
    limit: Annotated[int, Query(ge=1, le=5)] = 5,
) -> LocationResolutionResponse:
    """Return bounded canonical options without an LLM or MCP call."""

    del principal
    options = await service.resolve(query=query, max_results=limit)
    return LocationResolutionResponse(query=query, options=options)
