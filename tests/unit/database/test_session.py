"""Tests for async database session configuration."""

import asyncio
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
        jwt_signing_key=SecretStr("test-signing-key"),
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
        jwt_signing_key=SecretStr("test-signing-key"),
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
        jwt_signing_key=SecretStr("test-signing-key"),
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
