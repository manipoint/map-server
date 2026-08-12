"""Integration tests for CORS policy."""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

ALLOWED_ORIGIN = "https://flutter.example"


def create_test_client() -> TestClient:
    """Create an application with one allowed frontend origin."""

    settings = get_settings().model_copy(update={"cors_origins": [ALLOWED_ORIGIN]})

    return TestClient(create_app(settings))


def test_cors_allows_configured_origin() -> None:
    """A configured frontend origin should pass preflight."""

    with create_test_client() as client:
        response = client.options(
            "/health/live",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_unknown_origin() -> None:
    """An unknown frontend origin should fail preflight."""

    with create_test_client() as client:
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://malicious.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
