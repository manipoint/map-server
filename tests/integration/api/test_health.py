"""Integration tests for application health endpoints."""

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import get_settings


def test_application_liveness_endpoint() -> None:
    """The configured application should expose its liveness endpoint."""

    with TestClient(main_module.app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_app_configures_logging(monkeypatch) -> None:
    """Application creation should configure the selected log level."""

    configured_levels: list[str] = []
    settings = get_settings().model_copy(update={"log_level": "WARNING"})

    monkeypatch.setattr(
        main_module,
        "configure_logging",
        configured_levels.append,
    )

    application = main_module.create_app(settings)

    assert configured_levels == ["WARNING"]
    assert application.state.settings is settings
