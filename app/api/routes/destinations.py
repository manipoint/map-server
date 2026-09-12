"""Authenticated destination View All, detail, place, and gallery routes."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.dependencies import (
    CurrentPrincipal,
    DestinationCatalogueServiceDependency,
)
from app.api.schemas.destinations import (
    DestinationDetailResponse,
    DestinationListResponse,
    DestinationPlaceDetailResponse,
    DestinationPlaceListResponse,
)
from app.domain.destinations import DestinationCollection
from app.domain.preferences import RecommendationScope

router = APIRouter(prefix="/destinations", tags=["destinations"])


@router.get(
    "",
    response_model=DestinationListResponse,
    summary="View all destinations in a ranked collection",
)
async def list_destinations(
    principal: CurrentPrincipal,
    service: DestinationCatalogueServiceDependency,
    response: Response,
    collection: DestinationCollection = DestinationCollection.SUGGESTED,
    scope: RecommendationScope | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> DestinationListResponse:
    """Return a bounded cursor page using backend-owned ranking rules."""

    page = await service.list_destinations(
        user_id=principal.user.id,
        collection=collection,
        scope=scope,
        limit=limit,
        cursor=cursor,
    )
    response.headers["Cache-Control"] = "private, max-age=300"
    return DestinationListResponse.from_page(page)


@router.get(
    "/{destination_slug}/places/{place_slug}",
    response_model=DestinationPlaceDetailResponse,
    summary="Get one curated destination place",
)
async def get_destination_place(
    destination_slug: str,
    place_slug: str,
    principal: CurrentPrincipal,
    service: DestinationCatalogueServiceDependency,
    response: Response,
) -> DestinationPlaceDetailResponse:
    """Return exact place coordinates, copy, and ordered gallery."""

    del principal
    detail = await service.get_place(
        destination_slug=destination_slug,
        place_slug=place_slug,
    )
    response.headers["Cache-Control"] = "private, max-age=300"
    return DestinationPlaceDetailResponse.from_detail(detail)


@router.get(
    "/{destination_slug}/places",
    response_model=DestinationPlaceListResponse,
    summary="View all curated places for a destination",
)
async def list_destination_places(
    destination_slug: str,
    principal: CurrentPrincipal,
    service: DestinationCatalogueServiceDependency,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> DestinationPlaceListResponse:
    """Return a bounded cursor page of published places."""

    del principal
    page = await service.list_places(
        destination_slug=destination_slug,
        limit=limit,
        cursor=cursor,
    )
    response.headers["Cache-Control"] = "private, max-age=300"
    return DestinationPlaceListResponse.from_page(page)


@router.get(
    "/{destination_slug}",
    response_model=DestinationDetailResponse,
    summary="Get destination overview, map, gallery, and places",
)
async def get_destination(
    destination_slug: str,
    principal: CurrentPrincipal,
    service: DestinationCatalogueServiceDependency,
    response: Response,
) -> DestinationDetailResponse:
    """Return complete bounded editorial detail for one destination."""

    del principal
    detail = await service.get_destination(slug=destination_slug)
    response.headers["Cache-Control"] = "private, max-age=300"
    return DestinationDetailResponse.from_detail(detail)
