"""Async database engine and session management."""

from typing import Any

import asyncpg
from google.cloud.sql.connector import Connector, IPTypes, create_async_connector
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create the application PostgreSQL engine and connection pool."""
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required to create a URL engine")

    return create_async_engine(
        settings.database_url.get_secret_value(), **create_engine_options(settings)
    )


def create_engine_options(settings: Settings) -> dict[str, Any]:
    """Return shared SQLAlchemy connection-pool options."""
    return {
        "echo": settings.database_echo,
        "pool_pre_ping": True,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "pool_recycle": settings.database_pool_recycle_seconds,
    }


def create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    """Create a factory that produces independent async sessions."""
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, autoflush=False, expire_on_commit=False
    )


async def create_cloud_sql_resources(
    settings: Settings,
) -> tuple[AsyncEngine, Connector]:
    """Create a Cloud SQL connector and its SQLAlchemy engine."""
    if settings.cloud_sql_instance_connection_name is None:
        raise ValueError("Cloud SQL instance connection name is required")
    if settings.database_user is None:
        raise ValueError("Cloud SQL database user is required")
    if settings.database_name is None:
        raise ValueError("Cloud SQL database name is required")
    if settings.database_password is None:
        raise ValueError("Cloud SQL database password is required")

    ip_type = (
        IPTypes.PRIVATE if settings.cloud_sql_ip_type == "private" else IPTypes.PUBLIC
    )
    connector = await create_async_connector(
        ip_type=ip_type,
        refresh_strategy="LAZY",
    )

    async def create_connection() -> asyncpg.Connection:
        """Open one asyncpg connection through the Cloud SQL connector."""

        connection: asyncpg.Connection = await connector.connect_async(
            settings.cloud_sql_instance_connection_name,
            "asyncpg",
            user=settings.database_user,
            password=settings.database_password.get_secret_value(),
            db=settings.database_name,
        )
        return connection

    try:
        engine = create_async_engine(
            "postgresql+asyncpg://",
            async_creator=create_connection,
            **create_engine_options(settings),
        )
    except BaseException:
        await connector.close_async()
        raise

    return engine, connector
