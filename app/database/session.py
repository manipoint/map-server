"""Async database engine and session management."""

import ssl
from typing import Any

import asyncpg
import certifi
from google.cloud.sql.connector import Connector, IPTypes, create_async_connector
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
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

    try:
        url = make_url(settings.database_url.get_secret_value())
    except (ArgumentError, ValueError):
        raise ValueError("DATABASE_URL is not a valid SQLAlchemy URL") from None
    if url.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("DATABASE_URL must use PostgreSQL with asyncpg")
    url = url.set(drivername="postgresql+asyncpg")
    if "channel_binding" in url.query:
        # asyncpg cannot enforce libpq's channel-binding policy. Never silently
        # discard a required authentication setting.
        raise ValueError(
            "asyncpg does not support channel_binding; configure a URL without "
            "that option and use sslmode=verify-full"
        )
    mode = url.query.get("sslmode")
    options: dict[str, Any] = {
        "timeout": settings.database_connect_timeout_seconds,
        "command_timeout": settings.database_command_timeout_seconds,
    }
    if mode is not None:
        if "ssl" in url.query:
            raise ValueError("Use only one of ssl or sslmode in DATABASE_URL")
        if mode not in {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("Unsupported DATABASE_URL sslmode")
        url = url.difference_update_query(["sslmode"])
        # Hosted database credentials must only go to a verified server. Upgrade
        # require to certificate + hostname verification rather than CERT_NONE.
        options["ssl"] = (
            ssl.create_default_context(cafile=certifi.where())
            if mode in {"require", "verify-full"}
            else mode
        )
    return create_async_engine(
        url, connect_args=options, **create_engine_options(settings)
    )


def create_engine_options(settings: Settings) -> dict[str, Any]:
    """Return shared SQLAlchemy connection-pool options."""
    return {
        "echo": settings.database_echo,
        "hide_parameters": True,
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
