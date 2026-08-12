"""Tests for FastAPI dependency providers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_database_session


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
