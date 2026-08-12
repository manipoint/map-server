"""Request-scoped observability context."""

from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


def get_request_id() -> str | None:
    """Return the request ID for the current async context."""

    return _request_id.get()


def set_request_id(value: str) -> Token[str | None]:
    """Set the request ID and return its reset token."""

    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request-ID context."""

    _request_id.reset(token)
