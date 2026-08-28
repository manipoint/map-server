"""Trip routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.dependencies import CurrentPrincipal, TripServiceDependency
from app.api.schemas.trips import (
    TripCreateRequest,
    TripListResponse,
    TripResponse,
    TripUpdateRequest,
)
from app.domain.trips import TripStatus

router = APIRouter(
    prefix="/trips",
    tags=["trips"],
)


@router.get(
    "",
    response_model=TripListResponse,
    summary="List current user's trips",
)
async def list_user_trips(
    principal: CurrentPrincipal,
    trip_service: TripServiceDependency,
    status_filter: Annotated[
        TripStatus | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=50),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=512),
    ] = None,
) -> TripListResponse:
    """Return one cursor-paginated page of the user's trips."""
    result = await trip_service.list_trips(
        user_id=principal.user.id,
        status=status_filter,
        limit=limit,
        cursor=cursor,
    )
    return TripListResponse(
        items=[TripResponse.model_validate(trip) for trip in result.items],
        next_cursor=result.next_cursor,
    )


@router.get(
    "/{trip_id}",
    response_model=TripResponse,
    summary="Get one trip",
)
async def get_user_trip(
    trip_id: UUID,
    principal: CurrentPrincipal,
    trip_service: TripServiceDependency,
) -> TripResponse:
    """Return one trip owned by the authenticated user."""

    trip = await trip_service.get_trip(
        trip_id=trip_id,
        user_id=principal.user.id,
    )
    return TripResponse.model_validate(trip)


@router.post(
    "",
    response_model=TripResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft trip",
)
async def create_user_trip(
    payload: TripCreateRequest,
    principal: CurrentPrincipal,
    trip_service: TripServiceDependency,
) -> TripResponse:
    """Create a draft trip owned by the authenticated user."""

    trip = await trip_service.create_trip(
        user_id=principal.user.id,
        title=payload.title,
        origin=payload.origin,
        destination=payload.destination,
        origin_location=payload.origin_location,
        destination_location=payload.destination_location,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    return TripResponse.model_validate(trip)


@router.post(
    "/{trip_id}/plan",
    response_model=TripResponse,
    summary="Mark a trip as planned",
)
async def plan_user_trip(
    trip_id: UUID,
    principal: CurrentPrincipal,
    trip_service: TripServiceDependency,
) -> TripResponse:
    """Transition an authenticated user's draft trip to planned."""
    trip = await trip_service.mark_trip_planned(
        trip_id=trip_id,
        user_id=principal.user.id,
    )
    return TripResponse.model_validate(trip)


@router.post(
    "/{trip_id}/archive",
    response_model=TripResponse,
    summary="Archive one trip",
)
async def archive_user_trip(
    trip_id: UUID,
    principal: CurrentPrincipal,
    trip_service: TripServiceDependency,
) -> TripResponse:
    """Archive one trip owned by the authenticated user."""

    trip = await trip_service.archive_trip(
        trip_id=trip_id,
        user_id=principal.user.id,
    )

    return TripResponse.model_validate(trip)


@router.patch(
    "/{trip_id}",
    response_model=TripResponse,
    summary="Update one trip",
)
async def update_user_trip(
    trip_id: UUID,
    payload: TripUpdateRequest,
    principal: CurrentPrincipal,
    trip_service: TripServiceDependency,
) -> TripResponse:
    """Partially update one trip owned by the authenticated user."""

    trip = await trip_service.update_trip(
        trip_id=trip_id,
        user_id=principal.user.id,
        update=payload,
    )

    return TripResponse.model_validate(trip)


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Permanently delete one trip",
)
async def delete_user_trip(
    trip_id: UUID,
    principal: CurrentPrincipal,
    trip_service: TripServiceDependency,
) -> Response:
    """Permanently delete one trip owned by the authenticated user."""

    await trip_service.delete_trip(
        trip_id=trip_id,
        user_id=principal.user.id,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
