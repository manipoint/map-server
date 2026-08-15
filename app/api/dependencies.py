"""FastAPI dependency providers."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import InvalidAccessTokenError
from app.auth.service import (
    AuthenticatedPrincipal,
    AuthService,
)
from app.config import Settings
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
bearer_scheme = HTTPBearer(
    auto_error=False,
)


def get_auth_service(
    request: Request,
    database_session: DatabaseSession,
) -> AuthService:
    """Create an authentication service for one HTTP request."""

    settings: Settings = request.app.state.settings

    return AuthService(
        session=database_session,
        settings=settings,
    )


AuthServiceDependency = Annotated[
    AuthService,
    Depends(get_auth_service),
]


BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]


async def get_current_principal(
    credentials: BearerCredentials,
    auth_service: AuthServiceDependency,
) -> AuthenticatedPrincipal:
    """Authenticate the request bearer token."""

    if credentials is None:
        raise InvalidAccessTokenError("Access token is required")

    if credentials.scheme.lower() != "bearer":
        raise InvalidAccessTokenError("Bearer access token is required")

    return await auth_service.authenticate_access_token(
        access_token=credentials.credentials,
    )


CurrentPrincipal = Annotated[
    AuthenticatedPrincipal,
    Depends(get_current_principal),
]
