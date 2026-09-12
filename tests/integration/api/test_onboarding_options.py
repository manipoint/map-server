"""Integration tests for backend-owned onboarding options."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.onboarding import router


def test_options_are_public_cacheable_and_versioned() -> None:
    """Flutter should render active choices without duplicating backend enums."""

    application = FastAPI()
    application.include_router(router, prefix="/api/v1")

    with TestClient(application) as client:
        response = client.get("/api/v1/onboarding/options")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"
    payload = response.json()
    assert payload["version"] == 1
    assert {item["id"] for item in payload["travel_styles"]} == {
        "adventure",
        "beaches",
        "culture",
        "food",
        "luxury",
        "nature",
    }
    assert any(item["id"] == "hiking" for item in payload["interests"])
