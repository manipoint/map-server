"""Integration tests for application lifespan."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.lifespan as lifespan_module
import app.main as main_module
from app.api.websocket.connection_manager import ConnectionManager
from app.config import Settings


@pytest.fixture(autouse=True)
def mock_travel_graph_construction(monkeypatch):
    """Keep lifespan tests independent from external provider configuration."""

    monkeypatch.setattr(lifespan_module, "build_model_gateway", MagicMock())
    monkeypatch.setattr(lifespan_module, "build_travel_graph", MagicMock())


def create_url_settings() -> Settings:
    """Create deterministic URL-mode settings for lifespan tests."""

    return Settings(
        _env_file=None,
        database_connection_mode="url",
        database_url=SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        weather_api_key=SecretStr("test-weather-api-key"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
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
        assert isinstance(application.state.connection_manager, ConnectionManager)

    fake_engine.dispose.assert_awaited_once()


def test_lifespan_builds_one_shared_travel_graph(monkeypatch) -> None:
    """Startup should construct the provider gateway and compiled graph once."""

    fake_engine = AsyncMock()
    fake_gateway = object()
    fake_graph = object()
    build_gateway = MagicMock(return_value=fake_gateway)
    build_graph = MagicMock(return_value=fake_graph)

    monkeypatch.setattr(
        lifespan_module,
        "create_database_engine",
        lambda settings: fake_engine,
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        lambda engine: object(),
    )
    monkeypatch.setattr(lifespan_module, "build_model_gateway", build_gateway)
    monkeypatch.setattr(lifespan_module, "build_travel_graph", build_graph)
    settings = create_url_settings()
    application = main_module.create_app(settings)

    with TestClient(application):
        assert application.state.travel_graph is fake_graph

    build_gateway.assert_called_once_with(settings=settings)
    build_graph.assert_called_once_with(model_gateway=fake_gateway)


def test_lifespan_exposes_and_closes_weather_mcp_resources(monkeypatch) -> None:
    """Startup should share weather resources and close their HTTP client."""

    fake_engine = AsyncMock()
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock()
    fake_weather_provider = object()
    fake_mcp_server = object()
    fake_mcp_client = object()
    create_http_client = MagicMock(return_value=fake_http_client)
    create_weather_provider = MagicMock(return_value=fake_weather_provider)
    create_server = MagicMock(return_value=fake_mcp_server)
    create_client = MagicMock(return_value=fake_mcp_client)

    monkeypatch.setattr(
        lifespan_module,
        "create_database_engine",
        lambda settings: fake_engine,
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        lambda engine: object(),
    )
    monkeypatch.setattr(lifespan_module.httpx, "AsyncClient", create_http_client)
    monkeypatch.setattr(
        lifespan_module,
        "WeatherApiClient",
        create_weather_provider,
    )
    monkeypatch.setattr(lifespan_module, "create_mcp_server", create_server)
    monkeypatch.setattr(lifespan_module, "TravelMcpClient", create_client)
    settings = create_url_settings()
    application = main_module.create_app(settings)

    with TestClient(application):
        assert application.state.http_client is fake_http_client
        assert application.state.weather_provider is fake_weather_provider
        assert application.state.mcp_server is fake_mcp_server
        assert application.state.mcp_client is fake_mcp_client

    create_http_client.assert_called_once_with()
    create_weather_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_server.assert_called_once_with(weather_provider=fake_weather_provider)
    create_client.assert_called_once_with(mcp_server=fake_mcp_server)
    fake_http_client.aclose.assert_awaited_once_with()


def test_lifespan_closes_websockets_before_disposing_database(
    monkeypatch,
) -> None:
    """Shutdown should stop live sockets before tearing down persistence."""

    operations: list[str] = []
    fake_engine = AsyncMock()
    fake_http_client = MagicMock()
    fake_manager = MagicMock(spec=ConnectionManager)

    async def close_all() -> int:
        operations.append("websockets_closed")
        return 2

    async def dispose_engine() -> None:
        operations.append("database_disposed")

    async def close_http_client() -> None:
        operations.append("http_client_closed")

    def fake_create_engine(settings):
        assert settings is not None
        return fake_engine

    def fake_create_session_factory(engine):
        assert engine is fake_engine
        return object()

    fake_manager.close_all = AsyncMock(side_effect=close_all)
    fake_engine.dispose = AsyncMock(side_effect=dispose_engine)
    fake_http_client.aclose = AsyncMock(side_effect=close_http_client)
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
    monkeypatch.setattr(
        lifespan_module,
        "ConnectionManager",
        lambda: fake_manager,
    )
    monkeypatch.setattr(
        lifespan_module.httpx,
        "AsyncClient",
        lambda: fake_http_client,
    )
    application = main_module.create_app(create_url_settings())

    with TestClient(application):
        assert application.state.connection_manager is fake_manager

    assert operations == [
        "websockets_closed",
        "http_client_closed",
        "database_disposed",
    ]
    fake_manager.close_all.assert_awaited_once_with()
    fake_http_client.aclose.assert_awaited_once_with()
    fake_engine.dispose.assert_awaited_once_with()


def test_lifespan_disposes_database_when_http_cleanup_fails(monkeypatch) -> None:
    """An HTTP cleanup failure must not leak the database engine."""

    fake_engine = AsyncMock()
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock(
        side_effect=RuntimeError("http client cleanup failed")
    )

    monkeypatch.setattr(
        lifespan_module,
        "create_database_engine",
        lambda settings: fake_engine,
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        lambda engine: object(),
    )
    monkeypatch.setattr(
        lifespan_module.httpx,
        "AsyncClient",
        lambda: fake_http_client,
    )
    application = main_module.create_app(create_url_settings())

    with pytest.raises(RuntimeError, match="http client cleanup failed"):
        with TestClient(application):
            pass

    fake_http_client.aclose.assert_awaited_once_with()
    fake_engine.dispose.assert_awaited_once_with()


def test_lifespan_creates_an_isolated_connection_manager_per_application(
    monkeypatch,
) -> None:
    """Separate application instances must not share active socket state."""

    fake_engines: list[AsyncMock] = []

    def fake_create_engine(settings):
        assert settings is not None
        engine = AsyncMock()
        fake_engines.append(engine)
        return engine

    def fake_create_session_factory(engine):
        assert engine in fake_engines
        return object()

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

    first_application = main_module.create_app(create_url_settings())
    second_application = main_module.create_app(create_url_settings())

    with TestClient(first_application):
        first_manager = first_application.state.connection_manager

    with TestClient(second_application):
        second_manager = second_application.state.connection_manager

    assert isinstance(first_manager, ConnectionManager)
    assert isinstance(second_manager, ConnectionManager)
    assert first_manager is not second_manager
    assert len(fake_engines) == 2
    for engine in fake_engines:
        engine.dispose.assert_awaited_once_with()


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
        weather_api_key=SecretStr("test-weather-api-key"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
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
        weather_api_key=SecretStr("test-weather-api-key"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
    )
    application = main_module.create_app(settings)

    with pytest.raises(RuntimeError, match="engine disposal failed"):
        with TestClient(application):
            pass

    fake_connector.close_async.assert_awaited_once_with()


def test_cloud_sql_cleanup_runs_when_websocket_cleanup_fails(monkeypatch) -> None:
    """A manager shutdown error must not leak database or connector resources."""

    fake_engine = AsyncMock()
    fake_connector = AsyncMock()
    fake_manager = MagicMock(spec=ConnectionManager)
    fake_manager.close_all = AsyncMock(
        side_effect=RuntimeError("websocket cleanup failed")
    )

    async def fake_create_cloud_sql_resources(settings):
        assert settings.database_connection_mode == "cloud_sql"
        return fake_engine, fake_connector

    def fake_create_session_factory(engine):
        assert engine is fake_engine
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
    monkeypatch.setattr(
        lifespan_module,
        "ConnectionManager",
        lambda: fake_manager,
    )
    settings = Settings(
        _env_file=None,
        database_connection_mode="cloud_sql",
        database_url=None,
        cloud_sql_instance_connection_name="project:region:instance",
        database_user="travel_app",
        database_name="travel_assistant",
        database_password=SecretStr("test-password"),
        weather_api_key=SecretStr("test-weather-api-key"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
    )
    application = main_module.create_app(settings)

    with pytest.raises(RuntimeError, match="websocket cleanup failed"):
        with TestClient(application):
            pass

    fake_manager.close_all.assert_awaited_once_with()
    fake_engine.dispose.assert_awaited_once_with()
    fake_connector.close_async.assert_awaited_once_with()
