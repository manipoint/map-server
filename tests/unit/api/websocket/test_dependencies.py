"""Unit tests for WebSocket authentication helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import WebSocket

from app.api.websocket.connection_manager import ConnectionManager
from app.api.websocket.dependencies import (
    extract_bearer_token,
    get_connection_manager,
)
from app.auth.exceptions import InvalidAccessTokenError


@pytest.mark.parametrize(
    ("authorization", "expected_token"),
    [
        ("Bearer signed-token", "signed-token"),
        ("bearer signed-token", "signed-token"),
        ("  Bearer   signed-token  ", "signed-token"),
    ],
)
def test_extract_bearer_token_accepts_a_valid_header(
    authorization: str,
    expected_token: str,
) -> None:
    """Bearer scheme matching should be case-insensitive and whitespace-safe."""

    assert extract_bearer_token(authorization) == expected_token


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Basic signed-token",
        "Bearer signed-token unexpected-value",
    ],
)
def test_extract_bearer_token_rejects_a_malformed_header(
    authorization: str | None,
) -> None:
    """Malformed authorization data must not reach token verification."""

    with pytest.raises(InvalidAccessTokenError):
        extract_bearer_token(authorization)


def test_get_connection_manager_returns_the_application_resource() -> None:
    """The dependency should expose the manager owned by this application."""

    connection_manager = ConnectionManager()
    websocket = MagicMock(spec=WebSocket)
    websocket.app = SimpleNamespace(
        state=SimpleNamespace(connection_manager=connection_manager)
    )

    assert get_connection_manager(websocket) is connection_manager
