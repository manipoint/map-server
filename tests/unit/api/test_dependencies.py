"""Tests for FastAPI dependency providers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_auth_service,
    get_connection_manager,
    get_current_principal,
    get_database_session,
    get_travel_response_service,
)
from app.api.websocket.connection_manager import ConnectionManager
from app.auth.exceptions import InvalidAccessTokenError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.config import Settings


def create_request(session_factory: MagicMock) -> Request:
    """Create a request with an application session factory."""

    application = FastAPI()
    application.state.session_factory = session_factory
    return Request(
        {
            "type": "http",
            "app": application,
        }
    )


def test_database_session_is_provided_and_closed() -> None:
    """A request should receive a session that is always closed."""
    session = AsyncMock(spec=AsyncSession)
    session_factory = MagicMock(return_value=session)
    request = create_request(session_factory)

    async def exercise_dependency() -> None:
        dependency = get_database_session(request=request)
        provided_session = await anext(dependency)

        assert provided_session is session
        await dependency.aclose()

    asyncio.run(exercise_dependency())
    session_factory.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once_with()


def test_database_session_rolls_back_after_failure() -> None:
    """An endpoint failure should roll back and close its session."""

    session = AsyncMock(spec=AsyncSession)
    session_factory = MagicMock(return_value=session)
    request = create_request(session_factory)

    async def exercise_dependency() -> None:
        dependency = get_database_session(request=request)
        await anext(dependency)
        with pytest.raises(RuntimeError, match="route failed"):
            await dependency.athrow(RuntimeError("route failed"))

    asyncio.run(exercise_dependency())
    session.rollback.assert_awaited_once_with()
    session.close.assert_awaited_once_with()


def test_auth_service_uses_request_settings_and_database_session() -> None:
    """The authentication service should use request-scoped dependencies."""

    application = FastAPI()
    settings = MagicMock(spec=Settings)
    application.state.settings = settings
    request = Request(
        {
            "type": "http",
            "app": application,
        }
    )
    database_session = AsyncMock(spec=AsyncSession)

    service = get_auth_service(
        request=request,
        database_session=database_session,
    )

    assert isinstance(service, AuthService)
    assert service.session is database_session
    assert service.settings is settings


def test_connection_manager_uses_the_application_resource() -> None:
    """HTTP routes should share the manager created by application lifespan."""

    application = FastAPI()
    connection_manager = ConnectionManager()
    application.state.connection_manager = connection_manager
    request = Request(
        {
            "type": "http",
            "app": application,
        }
    )

    assert get_connection_manager(request) is connection_manager


def test_travel_response_service_uses_shared_graph_and_request_session() -> None:
    """Travel responses should reuse the startup graph but keep DB work scoped."""

    application = FastAPI()
    settings = MagicMock(spec=Settings)
    settings.conversation_history_message_limit = 20
    settings.assistant_run_lease_seconds = 120
    settings.max_model_attempts = 3
    graph = object()
    application.state.settings = settings
    application.state.travel_graph = graph
    request = Request(
        {
            "type": "http",
            "app": application,
        }
    )
    database_session = AsyncMock(spec=AsyncSession)

    service = asyncio.run(
        get_travel_response_service(
            request=request,
            database_session=database_session,
        )
    )

    assert service.graph is graph
    assert service.processing.session is database_session
    assert service.processing.history_limit == 20
    assert service.assistant_run_lease_seconds == 120
    assert service.max_model_attempts == 3


def test_current_principal_rejects_missing_credentials() -> None:
    """A request without authorization credentials should be rejected."""

    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()

    with pytest.raises(InvalidAccessTokenError, match="required"):
        asyncio.run(
            get_current_principal(
                credentials=None,
                auth_service=auth_service,
            )
        )

    auth_service.authenticate_access_token.assert_not_awaited()


def test_current_principal_rejects_wrong_authorization_scheme() -> None:
    """A non-bearer authorization scheme should be rejected."""

    credentials = HTTPAuthorizationCredentials(
        scheme="Basic",
        credentials="encoded-credentials",
    )
    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock()

    with pytest.raises(InvalidAccessTokenError, match="Bearer"):
        asyncio.run(
            get_current_principal(
                credentials=credentials,
                auth_service=auth_service,
            )
        )

    auth_service.authenticate_access_token.assert_not_awaited()


def test_current_principal_forwards_bearer_token() -> None:
    """A bearer credential should resolve and return the trusted principal."""

    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="signed-access-token",
    )
    principal = MagicMock(spec=AuthenticatedPrincipal)
    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock(return_value=principal)

    result = asyncio.run(
        get_current_principal(
            credentials=credentials,
            auth_service=auth_service,
        )
    )

    assert result is principal
    auth_service.authenticate_access_token.assert_awaited_once_with(
        access_token="signed-access-token"
    )


def test_current_principal_propagates_authentication_failure() -> None:
    """A service authentication failure should propagate to the handler."""

    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="invalid-access-token",
    )
    auth_service = MagicMock(spec=AuthService)
    auth_service.authenticate_access_token = AsyncMock(
        side_effect=InvalidAccessTokenError("invalid token")
    )

    with pytest.raises(InvalidAccessTokenError, match="invalid token"):
        asyncio.run(
            get_current_principal(
                credentials=credentials,
                auth_service=auth_service,
            )
        )
