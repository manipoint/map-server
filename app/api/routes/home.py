"""Authenticated, database-only Home discovery route."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.dependencies import CurrentPrincipal, HomeDiscoveryServiceDependency
from app.api.schemas.home import HomeDiscoveryResponse

router = APIRouter(prefix="/home", tags=["home"])


@router.get(
    "",
    response_model=HomeDiscoveryResponse,
    summary="Get personalized Home discovery",
)
async def get_home_discovery(
    principal: CurrentPrincipal,
    service: HomeDiscoveryServiceDependency,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=6)] = 6,
) -> HomeDiscoveryResponse:
    """Render all Home sections with no LLM, MCP, or provider calls."""

    discovery = await service.get_home(
        user_id=principal.user.id,
        section_limit=limit,
    )
    response.headers["Cache-Control"] = "private, max-age=300"
    return HomeDiscoveryResponse.from_discovery(discovery)
