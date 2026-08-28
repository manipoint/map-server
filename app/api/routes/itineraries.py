"""Itinerary routes."""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import CurrentPrincipal, ItineraryServiceDependency
from app.api.schemas.itineraries import (
    ItineraryCreateRequest,
    ItineraryItemResponse,
    ItineraryResponse,
)
from app.database.repositories.itineraries import ItineraryDetails

router = APIRouter(tags=["itineraries"])


def _to_response(details: ItineraryDetails) -> ItineraryResponse:
    """Map persistence details to the stable public representation."""

    itinerary = details.itinerary
    return ItineraryResponse(
        id=itinerary.id,
        trip_id=itinerary.trip_id,
        version=itinerary.version,
        status=itinerary.status,
        created_at=itinerary.created_at,
        updated_at=itinerary.updated_at,
        items=[ItineraryItemResponse.model_validate(item) for item in details.items],
    )


@router.post(
    "/trips/{trip_id}/itineraries",
    response_model=ItineraryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an itinerary draft",
)
async def create_itinerary_draft(
    trip_id: UUID,
    payload: ItineraryCreateRequest,
    principal: CurrentPrincipal,
    itinerary_service: ItineraryServiceDependency,
) -> ItineraryResponse:
    """Create the next itinerary version for an owned trip."""

    details = await itinerary_service.create_draft(
        trip_id=trip_id,
        user_id=principal.user.id,
        items=payload.items,
    )
    return _to_response(details)


@router.get(
    "/itineraries/{itinerary_id}",
    response_model=ItineraryResponse,
    summary="Get one itinerary version",
)
async def get_itinerary(
    itinerary_id: UUID,
    principal: CurrentPrincipal,
    itinerary_service: ItineraryServiceDependency,
) -> ItineraryResponse:
    """Return one itinerary version owned by the current user."""

    details = await itinerary_service.get_itinerary(
        itinerary_id=itinerary_id,
        user_id=principal.user.id,
    )
    return _to_response(details)


@router.get(
    "/trips/{trip_id}/itinerary",
    response_model=ItineraryResponse,
    summary="Get a trip's saved itinerary",
)
async def get_saved_itinerary(
    trip_id: UUID,
    principal: CurrentPrincipal,
    itinerary_service: ItineraryServiceDependency,
) -> ItineraryResponse:
    """Return the current saved plan for an owned trip."""

    details = await itinerary_service.get_saved_itinerary(
        trip_id=trip_id,
        user_id=principal.user.id,
    )
    return _to_response(details)


@router.post(
    "/itineraries/{itinerary_id}/save",
    response_model=ItineraryResponse,
    summary="Save one itinerary version",
)
async def save_itinerary(
    itinerary_id: UUID,
    principal: CurrentPrincipal,
    itinerary_service: ItineraryServiceDependency,
) -> ItineraryResponse:
    """Save a draft and supersede the previously saved version."""

    details = await itinerary_service.save_itinerary(
        itinerary_id=itinerary_id,
        user_id=principal.user.id,
    )
    return _to_response(details)
