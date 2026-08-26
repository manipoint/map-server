"""Tests for health-check routes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.api.dependencies import get_database_engine
from app.api.routes.health import router

app = FastAPI()
app.include_router(router=router)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_health_dependencies():
    """Keep dependency overrides isolated between health-route tests."""

    yield
    app.dependency_overrides.clear()


def create_database_probe() -> tuple[MagicMock, AsyncMock, MagicMock]:
    """Create an engine and connection context for readiness tests."""

    connection = AsyncMock(spec=AsyncConnection)
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=None)
    engine = MagicMock(spec=AsyncEngine)
    engine.connect.return_value = connection_context
    return engine, connection, connection_context


def configure_readiness(
    *,
    engine: MagicMock,
    timeout_seconds: float = 0.1,
) -> None:
    """Configure deterministic readiness dependencies for the test app."""

    app.state.settings = SimpleNamespace(
        database_readiness_timeout_seconds=timeout_seconds,
    )
    app.dependency_overrides[get_database_engine] = lambda: engine


def test_liveness_check_returns_ok() -> None:
    """The liveness endpoint should report a responsive process."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_check_does_not_access_database() -> None:
    """Process liveness must remain independent from PostgreSQL."""

    engine, _, _ = create_database_probe()
    configure_readiness(engine=engine)

    response = client.get("/health/live")

    assert response.status_code == 200
    engine.connect.assert_not_called()


def test_readiness_check_returns_ready_after_database_probe() -> None:
    """A successful lightweight query should report readiness."""

    engine, connection, connection_context = create_database_probe()
    configure_readiness(engine=engine)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    engine.connect.assert_called_once_with()
    connection.execute.assert_awaited_once()
    statement = connection.execute.await_args.args[0]
    assert str(statement) == "SELECT 1"
    connection_context.__aexit__.assert_awaited_once()


def test_readiness_check_returns_unavailable_when_connection_fails() -> None:
    """A connection failure should produce a safe unavailable response."""

    engine, _, _ = create_database_probe()
    engine.connect.side_effect = RuntimeError("database connection detail")
    configure_readiness(engine=engine)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "database connection detail" not in response.text


def test_readiness_check_returns_unavailable_when_query_fails() -> None:
    """A failed probe query should close the connection and return 503."""

    engine, connection, connection_context = create_database_probe()
    connection.execute.side_effect = RuntimeError("database query detail")
    configure_readiness(engine=engine)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "database query detail" not in response.text
    connection_context.__aexit__.assert_awaited_once()


def test_readiness_check_returns_unavailable_after_timeout() -> None:
    """A stalled database probe should not exceed its bounded deadline."""

    engine, connection, connection_context = create_database_probe()

    async def wait_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    connection.execute.side_effect = wait_forever
    configure_readiness(engine=engine, timeout_seconds=0.01)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    connection_context.__aexit__.assert_awaited_once()
