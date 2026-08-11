"""Integration tests for application health endpoints."""

from fastapi.testclient import TestClient

from app.main import app


def test_application_liveness_endpoint() -> None:
    """The configured application should expose its liveness endpoint."""
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
