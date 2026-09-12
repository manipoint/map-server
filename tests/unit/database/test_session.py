"""Tests for async database session configuration."""

import asyncio
import ssl
from unittest.mock import AsyncMock, Mock

import pytest
from google.cloud.sql.connector import IPTypes
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

import app.database.session as session_module
from app.config import Settings, get_settings
from app.database.session import create_database_engine, create_session_factory


def create_database_settings():
    """Create database settings without connecting to PostgreSQL."""

    return get_settings().model_copy(
        update={
            "database_url": SecretStr(
                "postgresql+asyncpg://travel_user:secret@localhost/travel_test"
            ),
            "database_echo": False,
            "database_pool_size": 2,
            "database_max_overflow": 3,
        }
    )


def test_create_database_engine_uses_asyncpg() -> None:
    """The engine should use PostgreSQL's asyncpg dialect."""
    engine = create_database_engine(create_database_settings())
    try:
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.pool.size() == 2
        assert "secret" not in str(engine.url)

    finally:
        asyncio.run(engine.dispose())


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+asyncpg"])
@pytest.mark.parametrize("mode", ["require", "verify-full"])
def test_hosted_url_uses_verified_tls(monkeypatch, scheme, mode) -> None:
    factory = Mock()
    monkeypatch.setattr(session_module, "create_async_engine", factory)
    settings = create_database_settings().model_copy(
        update={
            "database_url": SecretStr(
                f"{scheme}://owner:p%40ss@db.example/db?sslmode={mode}"
            ),
        }
    )
    create_database_engine(settings)
    url = factory.call_args.args[0]
    options = factory.call_args.kwargs
    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "p@ss"
    assert "sslmode" not in url.query
    context = options["connect_args"]["ssl"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert options["connect_args"]["timeout"] == 15
    assert options["connect_args"]["command_timeout"] == 30
    assert options["hide_parameters"] is True


@pytest.mark.parametrize(
    "query",
    [
        "channel_binding=require",
        "sslmode=invalid",
        "sslmode=require&ssl=disable",
    ],
)
def test_invalid_tls_configuration_fails_before_connect(monkeypatch, query) -> None:
    factory = Mock()
    monkeypatch.setattr(session_module, "create_async_engine", factory)
    settings = create_database_settings().model_copy(
        update={
            "database_url": SecretStr(
                f"postgresql://owner:private-password@db.example/db?{query}"
            ),
        }
    )
    with pytest.raises(ValueError) as error:
        create_database_engine(settings)
    assert "private-password" not in str(error.value)
    factory.assert_not_called()


@pytest.mark.parametrize(
    "url", ["not-a-url-secret", "postgresql://u:secret@db:bad/db", "sqlite:///local"]
)
def test_invalid_database_url_is_redacted(url) -> None:
    settings = create_database_settings().model_copy(
        update={"database_url": SecretStr(url)}
    )
    with pytest.raises(ValueError) as error:
        create_database_engine(settings)
    assert "secret" not in str(error.value)


def test_local_url_does_not_force_tls(monkeypatch) -> None:
    factory = Mock()
    monkeypatch.setattr(session_module, "create_async_engine", factory)
    create_database_engine(create_database_settings())
    assert "ssl" not in factory.call_args.kwargs["connect_args"]


def test_create_cloud_sql_resources_uses_async_connector(
    monkeypatch,
) -> None:
    """Cloud SQL mode should create an asyncpg engine through the connector."""

    connector = AsyncMock()
    database_connection = object()
    connector.connect_async.return_value = database_connection

    fake_engine = object()
    captured_engine_options = {}

    async def fake_create_async_connector(**options):
        assert options["ip_type"] is IPTypes.PUBLIC
        assert options["refresh_strategy"] == "LAZY"
        return connector

    def fake_create_async_engine(url, **options):
        assert url == "postgresql+asyncpg://"
        captured_engine_options.update(options)
        return fake_engine

    monkeypatch.setattr(
        session_module,
        "create_async_connector",
        fake_create_async_connector,
    )
    monkeypatch.setattr(
        session_module,
        "create_async_engine",
        fake_create_async_engine,
    )

    settings = Settings(
        _env_file=None,
        database_connection_mode="cloud_sql",
        database_url=None,
        cloud_sql_instance_connection_name=(
            "travel-assistant-505317:asia-south1:free-trial-first-project"
        ),
        cloud_sql_ip_type="public",
        database_user="travel_app",
        database_name="travel_assistant",
        database_password=SecretStr("test-password"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
    )

    async def run_test() -> None:
        engine, returned_connector = await session_module.create_cloud_sql_resources(
            settings
        )

        assert engine is fake_engine
        assert returned_connector is connector

        async_creator = captured_engine_options["async_creator"]
        connection = await async_creator()

        assert connection is database_connection
        connector.connect_async.assert_awaited_once_with(
            settings.cloud_sql_instance_connection_name,
            "asyncpg",
            user="travel_app",
            password="test-password",
            db="travel_assistant",
        )

    asyncio.run(run_test())


def test_create_cloud_sql_resources_uses_private_ip(monkeypatch) -> None:
    """Private Cloud SQL mode should request the private network address."""

    connector = AsyncMock()

    async def fake_create_async_connector(**options):
        assert options["ip_type"] is IPTypes.PRIVATE
        return connector

    monkeypatch.setattr(
        session_module,
        "create_async_connector",
        fake_create_async_connector,
    )
    monkeypatch.setattr(session_module, "create_async_engine", Mock())

    settings = Settings(
        _env_file=None,
        database_connection_mode="cloud_sql",
        database_url=None,
        cloud_sql_instance_connection_name="project:region:instance",
        cloud_sql_ip_type="private",
        database_user="travel_app",
        database_name="travel_assistant",
        database_password=SecretStr("test-password"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
    )

    asyncio.run(session_module.create_cloud_sql_resources(settings))


def test_create_cloud_sql_resources_closes_connector_on_engine_failure(
    monkeypatch,
) -> None:
    """An engine creation failure should close its Cloud SQL connector."""

    connector = AsyncMock()

    async def fake_create_async_connector(**options):
        return connector

    def fail_to_create_engine(*args, **kwargs):
        raise RuntimeError("engine creation failed")

    monkeypatch.setattr(
        session_module,
        "create_async_connector",
        fake_create_async_connector,
    )
    monkeypatch.setattr(
        session_module,
        "create_async_engine",
        fail_to_create_engine,
    )

    settings = Settings(
        _env_file=None,
        database_connection_mode="cloud_sql",
        database_url=None,
        cloud_sql_instance_connection_name="project:region:instance",
        database_user="travel_app",
        database_name="travel_assistant",
        database_password=SecretStr("test-password"),
        jwt_signing_key=SecretStr("test-jwt-signing-key-0123456789abcdef"),
        refresh_token_hash_key=SecretStr("test-refresh-hash-key-0123456789abcdef"),
    )

    with pytest.raises(RuntimeError, match="engine creation failed"):
        asyncio.run(session_module.create_cloud_sql_resources(settings))

    connector.close_async.assert_awaited_once_with()


def test_create_session_factory_produces_async_sessions() -> None:
    """The session factory should produce an independent AsyncSession."""
    engine = create_database_engine(create_database_settings())
    session_factory = create_session_factory(engine)
    session = session_factory()
    try:
        assert isinstance(session, AsyncSession)
        assert session.bind is engine
        assert session.sync_session.expire_on_commit is False
        assert session.autoflush is False

    finally:

        async def cleanup() -> None:
            await session.close()
            await engine.dispose()

        asyncio.run(cleanup())
