"""FastAPI dependency providers."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionFactory


async def get_database_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Provide one database session for one HTTP request."""
    session_factory: AsyncSessionFactory = request.app.state.session_factory
    session = session_factory()

    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_database_session),
]
