"""Integration tests for application lifespan."""

import logging
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.lifespan as lifespan_module
import app.main as main_module


def test_application_lifespan_logs_startup_and_shutdown(caplog) -> None:
    """Application lifespan should log startup and shutdown."""

    with caplog.at_level(logging.INFO, logger="app.lifespan"):
        with TestClient(main_module.app):
            pass

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.lifespan"
    ]
    assert "Application started" in messages
    assert "Application stopped" in messages


def test_lifespan_creates_and_disposes_database_resources(
    monkeypatch,
) -> None:
    """Lifespan should expose database resources and dispose the engine."""

    fake_engine = AsyncMock()
    fake_session_factory = object()

    def fake_create_engine(settings):
        """Return the fake database engine."""

        assert settings is not None
        return fake_engine

    def fake_create_session_factory(engine):
        """Return the fake session factory."""

        assert engine is fake_engine
        return fake_session_factory

    monkeypatch.setattr(
        lifespan_module,
        "create_database_engine",
        fake_create_engine,
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        fake_create_session_factory,
    )

    application = main_module.create_app()

    with TestClient(application):
        assert application.state.database_engine is fake_engine
        assert application.state.session_factory is fake_session_factory

    fake_engine.dispose.assert_awaited_once()
