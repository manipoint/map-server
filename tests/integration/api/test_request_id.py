"""Integration tests for HTTP request IDs."""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app


def assert_valid_uuid(value: str) -> None:
    """Assert that a string contains a valid UUID."""

    assert str(UUID(value)) == value


def test_response_contains_generated_request_id() -> None:
    """A missing request ID should be generated."""

    with TestClient(app) as client:
        response = client.get("/health/live")

    request_id = response.headers["X-Request-ID"]

    assert_valid_uuid(request_id)


def test_valid_request_id_is_preserved() -> None:
    """A valid client request ID should be returned unchanged."""

    request_id = str(uuid4())

    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers={"X-Request-ID": request_id},
        )

    assert response.headers["X-Request-ID"] == request_id


def test_invalid_request_id_is_replaced() -> None:
    """An invalid client request ID should not be trusted."""

    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers={"X-Request-ID": "invalid-request-id"},
        )

    generated_request_id = response.headers["X-Request-ID"]

    assert generated_request_id != "invalid-request-id"
    assert_valid_uuid(generated_request_id)
