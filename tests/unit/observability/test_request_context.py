"""Tests for request-scoped observability context."""

from app.observability.request_context import (
    get_request_id,
    reset_request_id,
    set_request_id,
)


def test_request_id_defaults_to_none() -> None:
    """No request ID should exist outside a request context."""

    assert get_request_id() is None


def test_request_id_can_be_set_and_reset() -> None:
    """Resetting should restore the previous context value."""

    token = set_request_id("request-123")

    try:
        assert get_request_id() == "request-123"
    finally:
        reset_request_id(token)

    assert get_request_id() is None
