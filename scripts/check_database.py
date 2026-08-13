"""Verify the configured Cloud SQL database connection."""

import asyncio

from sqlalchemy import text

from app.config import get_settings
from app.database.session import create_cloud_sql_resources


async def check_database_connection() -> None:
    """Connect to Cloud SQL and execute a lightweight query."""
    settings = get_settings()

    if settings.database_connection_mode != "cloud_sql":
        raise RuntimeError("DATABASE_CONNECTION_MODE must be cloud_sql")

    engine, connector = await create_cloud_sql_resources(settings)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(""" SELECT
                        current_database(),
                        current_user,
                        version()
    """)
            )
            database_name, database_user, database_version = result.one()
            print(f"Database: {database_name}")
            print(f"User: {database_user}")
            print(f"Version: {database_version}")
            print("Cloud SQL connection successful")
    finally:
        try:
            await engine.dispose()
        finally:
            await connector.close_async()


if __name__ == "__main__":
    asyncio.run(check_database_connection())
