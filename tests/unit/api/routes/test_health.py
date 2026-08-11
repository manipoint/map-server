"""Tests for health-check routes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.health import router

app = FastAPI()
app.include_router(router=router)

client = TestClient(app)


def test_liveness_check_returns_ok() -> None:
    """The liveness endpoint should report a responsive process."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
