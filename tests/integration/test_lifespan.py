"""Integration tests for application lifespan."""

import logging
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.lifespan as lifespan_module
import app.main as main_module
from app.config import Settings


def create_url_settings() -> Settings:
    """Create deterministic URL-mode settings for lifespan tests."""

    return Settings(
        _env_file=None,
        database_connection_mode="url",
        database_url=SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        jwt_signing_key=SecretStr("test-signing-key"),
    )


def test_application_lifespan_logs_startup_and_shutdown(caplog, monkeypatch) -> None:
    """Application lifespan should log startup and shutdown."""

    def preserve_test_logging(log_level):
        """Keep pytest's log-capture handler installed."""

    monkeypatch.setattr(main_module, "configure_logging", preserve_test_logging)
    application = main_module.create_app(create_url_settings())

    with caplog.at_level(logging.INFO, logger="app.lifespan"):
        with TestClient(application):
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

    application = main_module.create_app(create_url_settings())

    with TestClient(application):
        assert application.state.database_engine is fake_engine
        assert application.state.session_factory is fake_session_factory

    fake_engine.dispose.assert_awaited_once()


def test_cloud_sql_lifespan_creates_and_closes_resources(monkeypatch) -> None:
    """Cloud SQL lifespan should expose and close all database resources."""

    fake_engine = AsyncMock()
    fake_connector = AsyncMock()
    fake_session_factory = object()

    async def fake_create_cloud_sql_resources(settings):
        assert settings.database_connection_mode == "cloud_sql"
        return fake_engine, fake_connector

    def fake_create_session_factory(engine):
        assert engine is fake_engine
        return fake_session_factory

    monkeypatch.setattr(
        lifespan_module,
        "create_cloud_sql_resources",
        fake_create_cloud_sql_resources,
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        fake_create_session_factory,
    )

    settings = Settings(
        _env_file=None,
        database_connection_mode="cloud_sql",
        database_url=None,
        cloud_sql_instance_connection_name="project:region:instance",
        database_user="travel_app",
        database_name="travel_assistant",
        database_password=SecretStr("test-password"),
        jwt_signing_key=SecretStr("test-signing-key"),
    )
    application = main_module.create_app(settings)

    with TestClient(application):
        assert application.state.database_engine is fake_engine
        assert application.state.session_factory is fake_session_factory

    fake_engine.dispose.assert_awaited_once_with()
    fake_connector.close_async.assert_awaited_once_with()


def test_cloud_sql_lifespan_closes_connector_when_engine_disposal_fails(
    monkeypatch,
) -> None:
    """Connector cleanup should run even when engine disposal fails."""

    fake_engine = AsyncMock()
    fake_engine.dispose.side_effect = RuntimeError("engine disposal failed")
    fake_connector = AsyncMock()

    async def fake_create_cloud_sql_resources(settings):
        return fake_engine, fake_connector

    def fake_create_session_factory(engine):
        return object()

    monkeypatch.setattr(
        lifespan_module,
        "create_cloud_sql_resources",
        fake_create_cloud_sql_resources,
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        fake_create_session_factory,
    )

    settings = Settings(
        _env_file=None,
        database_connection_mode="cloud_sql",
        database_url=None,
        cloud_sql_instance_connection_name="project:region:instance",
        database_user="travel_app",
        database_name="travel_assistant",
        database_password=SecretStr("test-password"),
        jwt_signing_key=SecretStr("test-signing-key"),
    )
    application = main_module.create_app(settings)

    with pytest.raises(RuntimeError, match="engine disposal failed"):
        with TestClient(application):
            pass

    fake_connector.close_async.assert_awaited_once_with()
