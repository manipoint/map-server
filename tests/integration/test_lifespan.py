"""Integration tests for application lifespan."""

import logging
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.lifespan as lifespan_module
import app.main as main_module
from app.api.websocket.connection_manager import ConnectionManager
from app.config import Settings


@pytest.mark.parametrize("flush_error", [None, RuntimeError("telemetry unavailable")])
def test_langsmith_flush_is_bounded_and_cleanup_survives_failure(
    monkeypatch, flush_error
) -> None:
    engine = AsyncMock()
    http_client = MagicMock()
    http_client.aclose = AsyncMock()
    manager = MagicMock()
    manager.close_all = AsyncMock(return_value=0)
    factory = MagicMock()
    factory.client.flush.side_effect = flush_error
    monkeypatch.setattr(
        lifespan_module, "create_database_engine", lambda settings: engine
    )
    monkeypatch.setattr(
        lifespan_module, "create_session_factory", lambda engine: object()
    )
    monkeypatch.setattr(lifespan_module.httpx, "AsyncClient", lambda: http_client)
    monkeypatch.setattr(lifespan_module, "ConnectionManager", lambda: manager)
    monkeypatch.setattr(
        lifespan_module, "create_langsmith_tracer_factory", lambda settings: factory
    )
    application = main_module.create_app(create_url_settings())

    with TestClient(application):
        assert application.state.langsmith_tracer_factory is factory

    factory.client.flush.assert_called_once_with(timeout=2.0)
    manager.close_all.assert_awaited_once()
    http_client.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.fixture(autouse=True)
def mock_travel_graph_construction(monkeypatch):
    """Keep lifespan tests independent from external provider configuration."""

    monkeypatch.setattr(lifespan_module, "build_model_gateway", MagicMock())
    monkeypatch.setattr(lifespan_module, "build_travel_graph", MagicMock())


def create_url_settings(**overrides: object) -> Settings:
    """Create deterministic URL-mode settings with optional overrides."""

    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "weather_api_key": SecretStr("test-weather-api-key"),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
    }
    values.update(overrides)
    return Settings(**values)


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
    build_graph.assert_called_once_with(
        model_gateway=fake_gateway,
        tools=(),
        research_service=ANY,
        standalone_service=ANY,
        max_tool_rounds=settings.max_tool_rounds,
        deal_discovery_service=None,
    )


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
        assert application.state.airport_provider is None
        assert application.state.airport_resolution_service is None
        assert application.state.flight_provider is None
        assert application.state.flight_search_preparation_service is None
        assert application.state.location_provider is None
        assert application.state.hotel_provider is None
        assert application.state.hotel_search_service is None
        assert application.state.place_provider is None
        assert application.state.place_search_service is None
        assert application.state.currency_provider is None
        assert application.state.mcp_server is fake_mcp_server
        assert application.state.mcp_client is fake_mcp_client

    create_http_client.assert_called_once_with()
    create_weather_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_server.assert_called_once_with(
        weather_provider=fake_weather_provider,
        airport_resolution_service=None,
        flight_search_service=None,
        hotel_search_service=None,
        place_search_service=None,
        currency_provider=None,
    )
    create_client.assert_called_once_with(mcp_server=fake_mcp_server)
    fake_http_client.aclose.assert_awaited_once_with()


def test_lifespan_wires_enabled_google_places_service_into_mcp(
    monkeypatch,
) -> None:
    """Google configuration should assemble one places service and graph tool."""

    fake_engine = AsyncMock()
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock()
    fake_weather_provider = object()
    fake_location_provider = object()
    fake_place_provider = object()
    fake_place_service = object()
    fake_mcp_server = object()
    fake_gateway = object()
    fake_graph = object()
    create_location_provider = MagicMock(return_value=fake_location_provider)
    create_place_provider = MagicMock(return_value=fake_place_provider)
    create_place_service = MagicMock(return_value=fake_place_service)
    create_server = MagicMock(return_value=fake_mcp_server)
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
    monkeypatch.setattr(
        lifespan_module.httpx,
        "AsyncClient",
        MagicMock(return_value=fake_http_client),
    )
    monkeypatch.setattr(
        lifespan_module,
        "WeatherApiClient",
        MagicMock(return_value=fake_weather_provider),
    )
    monkeypatch.setattr(
        lifespan_module,
        "WeatherApiLocationClient",
        create_location_provider,
    )
    monkeypatch.setattr(
        lifespan_module,
        "GooglePlacesClient",
        create_place_provider,
    )
    monkeypatch.setattr(
        lifespan_module,
        "PlaceSearchService",
        create_place_service,
    )
    monkeypatch.setattr(lifespan_module, "create_mcp_server", create_server)
    monkeypatch.setattr(lifespan_module, "build_model_gateway", build_gateway)
    monkeypatch.setattr(lifespan_module, "build_travel_graph", build_graph)
    settings = create_url_settings(
        places_provider="google",
        google_places_api_key=SecretStr("test-google-places-key"),
    )
    application = main_module.create_app(settings)

    with TestClient(application):
        assert application.state.location_provider is fake_location_provider
        assert application.state.place_provider is fake_place_provider
        assert application.state.place_search_service is fake_place_service
        assert application.state.hotel_provider is None
        assert application.state.hotel_search_service is None
        assert application.state.travel_graph is fake_graph

    create_location_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_place_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_place_service.assert_called_once_with(
        location_provider=fake_location_provider,
        place_provider=fake_place_provider,
    )
    create_server.assert_called_once_with(
        weather_provider=fake_weather_provider,
        airport_resolution_service=None,
        flight_search_service=None,
        hotel_search_service=None,
        place_search_service=fake_place_service,
        currency_provider=None,
    )
    build_gateway.assert_called_once_with(settings=settings)
    build_graph.assert_called_once_with(
        model_gateway=fake_gateway,
        tools=(),
        research_service=ANY,
        standalone_service=ANY,
        max_tool_rounds=settings.max_tool_rounds,
        deal_discovery_service=None,
    )
    fake_http_client.aclose.assert_awaited_once_with()


def test_lifespan_wires_enabled_currency_provider_into_mcp(monkeypatch) -> None:
    """Frankfurter configuration should add one shared provider and graph tool."""

    fake_engine = AsyncMock()
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock()
    fake_weather_provider = object()
    fake_currency_provider = object()
    fake_mcp_server = object()
    fake_gateway = object()
    fake_graph = object()
    create_currency_provider = MagicMock(return_value=fake_currency_provider)
    create_server = MagicMock(return_value=fake_mcp_server)
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
    monkeypatch.setattr(
        lifespan_module.httpx,
        "AsyncClient",
        MagicMock(return_value=fake_http_client),
    )
    monkeypatch.setattr(
        lifespan_module,
        "WeatherApiClient",
        MagicMock(return_value=fake_weather_provider),
    )
    monkeypatch.setattr(
        lifespan_module,
        "FrankfurterCurrencyClient",
        create_currency_provider,
    )
    monkeypatch.setattr(lifespan_module, "create_mcp_server", create_server)
    monkeypatch.setattr(lifespan_module, "build_model_gateway", build_gateway)
    monkeypatch.setattr(lifespan_module, "build_travel_graph", build_graph)
    settings = create_url_settings(currency_provider="frankfurter")
    application = main_module.create_app(settings)

    with TestClient(application):
        assert application.state.currency_provider is fake_currency_provider
        assert application.state.travel_graph is fake_graph

    create_currency_provider.assert_called_once_with(
        http_client=fake_http_client,
        settings=settings,
    )
    create_server.assert_called_once_with(
        weather_provider=fake_weather_provider,
        airport_resolution_service=None,
        flight_search_service=None,
        hotel_search_service=None,
        place_search_service=None,
        currency_provider=fake_currency_provider,
    )
    build_gateway.assert_called_once_with(settings=settings)
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


def test_serpapi_startup_wires_search_services(monkeypatch, tmp_path) -> None:
    """SerpApi adapters connect through existing normalized search services."""
    engine = AsyncMock()
    http_client = MagicMock()
    http_client.aclose = AsyncMock()
    metadata_provider = MagicMock(airport_codes=frozenset({"LHE", "JFK"}))
    airport_provider = MagicMock(airport_codes=frozenset({"LHE"}))
    flight_provider = object()
    hotel_provider = object()
    load_metadata = AsyncMock(return_value=metadata_provider)
    load_directory = AsyncMock(return_value=airport_provider)
    create_flight = MagicMock(return_value=flight_provider)
    create_hotel = MagicMock(return_value=hotel_provider)
    build_graph = MagicMock(return_value=object())

    monkeypatch.setattr(
        lifespan_module, "create_database_engine", lambda settings: engine
    )
    monkeypatch.setattr(
        lifespan_module, "create_session_factory", lambda engine: object()
    )
    monkeypatch.setattr(lifespan_module.httpx, "AsyncClient", lambda: http_client)
    monkeypatch.setattr(
        lifespan_module.LocalFlightMetadataProvider, "from_file", load_metadata
    )
    monkeypatch.setattr(
        lifespan_module.LocalAirportProvider, "from_file", load_directory
    )
    monkeypatch.setattr(lifespan_module, "SerpApiFlightClient", create_flight)
    monkeypatch.setattr(lifespan_module, "SerpApiHotelClient", create_hotel)
    monkeypatch.setattr(lifespan_module, "build_travel_graph", build_graph)

    metadata_path = tmp_path / "flight_metadata.json"
    directory_path = tmp_path / "airport_directory.json"
    settings = create_url_settings(
        places_provider=None,
        flight_provider="serpapi",
        hotel_provider="serpapi",
        serpapi_api_key=SecretStr("test-serpapi-key"),
        flight_metadata_path=str(metadata_path),
        airport_directory_path=str(directory_path),
    )
    application = main_module.create_app(settings)

    with TestClient(application):
        state = application.state
        assert state.airport_provider is airport_provider
        assert state.flight_provider is flight_provider
        assert state.airport_resolution_service.airport_provider is airport_provider
        assert state.flight_search_service.flight_provider is flight_provider
        assert state.flight_search_service.supports_round_trip is True
        assert state.hotel_provider is hotel_provider
        assert state.hotel_search_service.hotel_provider is hotel_provider
        assert state.hotel_search_service.location_provider is state.location_provider
        assert state.flight_search_preparation_service.flight_search_service is (
            state.flight_search_service
        )
        deal_service = build_graph.call_args.kwargs["deal_discovery_service"]
        assert deal_service.airports is state.airport_resolution_service
        assert deal_service.deals.client.http_client is http_client
        load_metadata.assert_awaited_once_with(path=metadata_path)
        load_directory.assert_awaited_once_with(path=directory_path)
        create_flight.assert_called_once_with(
            http_client=http_client,
            metadata_provider=metadata_provider,
            settings=settings,
        )
        create_hotel.assert_called_once_with(
            http_client=http_client,
            settings=settings,
        )

    http_client.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()
