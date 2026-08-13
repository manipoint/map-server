"""Alembic migration environment."""

import asyncio
from logging.config import fileConfig

from google.cloud.sql.connector import Connector
from sqlalchemy.engine import Connection

import app.database.models  # noqa: F401
from alembic import context
from app.config import Settings, get_settings
from app.database.base import Base
from app.database.session import (
    create_cloud_sql_resources,
    create_database_engine,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings: Settings = get_settings()
target_metadata = Base.metadata


def configure_migration_context(connection: Connection) -> None:
    """Configure Alembic against an active database connection."""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Generate SQL without opening a database connection."""

    if settings.database_connection_mode == "url":
        if settings.database_url is None:
            raise RuntimeError("DATABASE_URL is required for offline migrations")

        database_url = settings.database_url.get_secret_value()
    else:
        database_url = "postgresql+asyncpg://"

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations through the configured async database connection."""

    connector: Connector | None = None

    if settings.database_connection_mode == "cloud_sql":
        engine, connector = await create_cloud_sql_resources(settings)
    else:
        engine = create_database_engine(settings)

    try:
        async with engine.connect() as connection:
            await connection.run_sync(configure_migration_context)
    finally:
        try:
            await engine.dispose()
        finally:
            if connector is not None:
                await connector.close_async()


def run_migrations_online() -> None:
    """Run migrations against the configured database."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
