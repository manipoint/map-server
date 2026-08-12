"""Tests for async database session configuration."""

import asyncio

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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
