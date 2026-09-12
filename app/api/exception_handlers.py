"""HTTP exception handlers for application-domain errors."""

from dataclasses import dataclass

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.auth.exceptions import (
    AccountNotActiveError,
    AuthenticationError,
    EmailAlreadyRegisteredError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
    SessionRevokedError,
)
from app.common.exceptions import InvalidCursorError, ProviderError
from app.domain.errors import (
    DestinationError,
    DestinationNotFoundError,
    DestinationPlaceNotFoundError,
    InvalidItineraryDetailsError,
    InvalidItineraryStatusTransitionError,
    InvalidTripDetailsError,
    InvalidTripStatusTransitionError,
    ItineraryError,
    ItineraryNotFoundError,
    TripError,
    TripNotFoundError,
)


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """Safe HTTP representation of a domain error."""

    status_code: int
    code: str
    message: str


DESTINATION_ERROR_DEFINITIONS: dict[type[DestinationError], ErrorDefinition] = {
    DestinationNotFoundError: ErrorDefinition(
        status_code=status.HTTP_404_NOT_FOUND,
        code="destination_not_found",
        message="Destination was not found",
    ),
    DestinationPlaceNotFoundError: ErrorDefinition(
        status_code=status.HTTP_404_NOT_FOUND,
        code="destination_place_not_found",
        message="Destination place was not found",
    ),
}


async def destination_exception_handler(
    request: Request,
    error: DestinationError,
) -> JSONResponse:
    """Convert catalogue-domain errors into safe public responses."""

    definition = DESTINATION_ERROR_DEFINITIONS.get(
        type(error),
        ErrorDefinition(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="destination_operation_failed",
            message="Destination operation failed",
        ),
    )
    return JSONResponse(
        status_code=definition.status_code,
        content={
            "error": {
                "code": definition.code,
                "message": definition.message,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


AUTH_ERROR_DEFINITIONS: dict[
    type[AuthenticationError],
    ErrorDefinition,
] = {
    EmailAlreadyRegisteredError: ErrorDefinition(
        status_code=status.HTTP_409_CONFLICT,
        code="email_already_registered",
        message="An account with this email already exists",
    ),
    InvalidCredentialsError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_credentials",
        message="Invalid email or password",
    ),
    InvalidAccessTokenError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_access_token",
        message="Invalid or expired access token",
    ),
    InvalidRefreshTokenError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_refresh_token",
        message="Invalid or expired refresh token",
    ),
    SessionRevokedError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="session_revoked",
        message="Authentication session is no longer active",
    ),
    RefreshTokenReuseError: ErrorDefinition(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="refresh_token_reuse",
        message="Authentication is required",
    ),
    AccountNotActiveError: ErrorDefinition(
        status_code=status.HTTP_403_FORBIDDEN,
        code="account_not_active",
        message="User account is not active",
    ),
}


async def authentication_exception_handler(
    request: Request,
    error: AuthenticationError,
) -> JSONResponse:
    """Convert an authentication-domain error into a safe response."""

    definition = AUTH_ERROR_DEFINITIONS.get(
        type(error),
        ErrorDefinition(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="authentication_failed",
            message="Authentication failed",
        ),
    )

    headers = None
    if definition.status_code == status.HTTP_401_UNAUTHORIZED:
        headers = {"WWW-Authenticate": "Bearer"}

    return JSONResponse(
        status_code=definition.status_code,
        headers=headers,
        content={
            "error": {
                "code": definition.code,
                "message": definition.message,
                "request_id": getattr(
                    request.state,
                    "request_id",
                    None,
                ),
            }
        },
    )


TRIP_ERROR_DEFINITIONS: dict[
    type[TripError],
    ErrorDefinition,
] = {
    TripNotFoundError: ErrorDefinition(
        status_code=status.HTTP_404_NOT_FOUND,
        code="trip_not_found",
        message="Trip was not found",
    ),
    InvalidTripDetailsError: ErrorDefinition(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_trip_details",
        message="Trip details are invalid",
    ),
    InvalidTripStatusTransitionError: ErrorDefinition(
        status_code=status.HTTP_409_CONFLICT,
        code="invalid_trip_status_transition",
        message="Trip status transition is not allowed",
    ),
}


async def trip_exception_handler(
    request: Request,
    error: TripError,
) -> JSONResponse:
    """Convert a trip-domain error into a safe HTTP response."""

    definition = TRIP_ERROR_DEFINITIONS.get(
        type(error),
        ErrorDefinition(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="trip_operation_failed",
            message="Trip operation failed",
        ),
    )

    return JSONResponse(
        status_code=definition.status_code,
        content={
            "error": {
                "code": definition.code,
                "message": definition.message,
                "request_id": getattr(
                    request.state,
                    "request_id",
                    None,
                ),
            }
        },
    )


ITINERARY_ERROR_DEFINITIONS: dict[
    type[ItineraryError],
    ErrorDefinition,
] = {
    ItineraryNotFoundError: ErrorDefinition(
        status_code=status.HTTP_404_NOT_FOUND,
        code="itinerary_not_found",
        message="Itinerary was not found",
    ),
    InvalidItineraryDetailsError: ErrorDefinition(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_itinerary_details",
        message="Itinerary details are invalid",
    ),
    InvalidItineraryStatusTransitionError: ErrorDefinition(
        status_code=status.HTTP_409_CONFLICT,
        code="invalid_itinerary_status_transition",
        message="Itinerary status transition is not allowed",
    ),
}


async def itinerary_exception_handler(
    request: Request,
    error: ItineraryError,
) -> JSONResponse:
    """Convert an itinerary-domain error into a safe HTTP response."""

    definition = ITINERARY_ERROR_DEFINITIONS.get(
        type(error),
        ErrorDefinition(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="itinerary_operation_failed",
            message="Itinerary operation failed",
        ),
    )

    return JSONResponse(
        status_code=definition.status_code,
        content={
            "error": {
                "code": definition.code,
                "message": definition.message,
                "request_id": getattr(
                    request.state,
                    "request_id",
                    None,
                ),
            }
        },
    )


async def invalid_cursor_exception_handler(
    request: Request,
    error: InvalidCursorError,
) -> JSONResponse:
    """Return a safe response for an invalid pagination cursor."""

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": {
                "code": "invalid_cursor",
                "message": "Pagination cursor is invalid",
                "request_id": getattr(
                    request.state,
                    "request_id",
                    None,
                ),
            }
        },
    )


async def provider_exception_handler(
    request: Request,
    error: ProviderError,
) -> JSONResponse:
    """Return a safe temporary-unavailability response for provider failures."""

    del error
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": {
                "code": "provider_unavailable",
                "message": "External provider is temporarily unavailable",
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )
