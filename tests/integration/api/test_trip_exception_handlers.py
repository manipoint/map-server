"""Integration tests for trip and pagination exception responses."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exception_handlers import (
    invalid_cursor_exception_handler,
    trip_exception_handler,
)
from app.api.middleware.request_id import RequestIdMiddleware
from app.common.exceptions import InvalidCursorError
from app.domain.errors import (
    InvalidTripDetailsError,
    InvalidTripStatusTransitionError,
    TripError,
    TripNotFoundError,
)


def create_error_app(error: Exception) -> FastAPI:
    """Create an application whose endpoint raises one application error."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(TripError, trip_exception_handler)
    application.add_exception_handler(
        InvalidCursorError,
        invalid_cursor_exception_handler,
    )

    @application.get("/raise-error")
    async def raise_error() -> None:
        raise error

    return application


@pytest.mark.parametrize(
    ("error_type", "expected_status", "expected_code", "expected_message"),
    [
        (
            TripNotFoundError,
            404,
            "trip_not_found",
            "Trip was not found",
        ),
        (
            InvalidTripDetailsError,
            422,
            "invalid_trip_details",
            "Trip details are invalid",
        ),
        (
            InvalidTripStatusTransitionError,
            409,
            "invalid_trip_status_transition",
            "Trip status transition is not allowed",
        ),
    ],
)
def test_trip_errors_have_safe_http_responses(
    error_type: type[TripError],
    expected_status: int,
    expected_code: str,
    expected_message: str,
) -> None:
    """Known trip errors should map to stable non-sensitive responses."""

    request_id = str(uuid4())
    sensitive_message = "sensitive internal trip detail"
    application = create_error_app(error_type(sensitive_message))

    with TestClient(application) as client:
        response = client.get(
            "/raise-error",
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == expected_status
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": expected_message,
            "request_id": request_id,
        }
    }
    assert sensitive_message not in response.text
    assert response.headers["X-Request-ID"] == request_id


def test_unknown_trip_error_uses_safe_fallback() -> None:
    """An unmapped trip error should use the generic operation response."""

    application = create_error_app(TripError("private failure detail"))

    with TestClient(application) as client:
        response = client.get("/raise-error")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "trip_operation_failed"
    assert response.json()["error"]["message"] == "Trip operation failed"
    assert "private failure detail" not in response.text


def test_invalid_cursor_has_safe_http_response() -> None:
    """Malformed pagination state should use the stable cursor error contract."""

    request_id = str(uuid4())
    application = create_error_app(
        InvalidCursorError("private decoder implementation detail")
    )

    with TestClient(application) as client:
        response = client.get(
            "/raise-error",
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_cursor",
            "message": "Pagination cursor is invalid",
            "request_id": request_id,
        }
    }
    assert "private decoder implementation detail" not in response.text
