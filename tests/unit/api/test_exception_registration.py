"""Tests for application exception-handler registration."""

from app.api.exception_handlers import (
    authentication_exception_handler,
    invalid_cursor_exception_handler,
    itinerary_exception_handler,
    provider_exception_handler,
    trip_exception_handler,
)
from app.auth.exceptions import AuthenticationError
from app.common.exceptions import InvalidCursorError, ProviderError
from app.domain.errors import ItineraryError, TripError
from app.main import app


def test_application_registers_domain_exception_handlers() -> None:
    """The production application should expose every safe domain mapping."""

    assert (
        app.exception_handlers[AuthenticationError] is authentication_exception_handler
    )
    assert app.exception_handlers[TripError] is trip_exception_handler
    assert app.exception_handlers[ItineraryError] is itinerary_exception_handler
    assert (
        app.exception_handlers[InvalidCursorError] is invalid_cursor_exception_handler
    )
    assert app.exception_handlers[ProviderError] is provider_exception_handler
