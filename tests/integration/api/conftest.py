"""Keep API transport tests independent of external provider credentials."""

from unittest.mock import MagicMock

import pytest

import app.lifespan as lifespan_module


@pytest.fixture(autouse=True)
def mock_external_startup_clients(monkeypatch):
    """Transport tests do not exercise weather or model provider adapters."""
    monkeypatch.setattr(lifespan_module, "WeatherApiClient", MagicMock())
    monkeypatch.setattr(lifespan_module, "build_model_gateway", MagicMock())
