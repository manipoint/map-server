"""Request-scoped observability context."""

from asyncio import Timeout
from contextvars import ContextVar, Token
from dataclasses import dataclass

_request_id: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Identifiers shared by one graph execution and its nested MCP calls."""

    trace_id: str
    conversation_id: str
    client_message_id: str
    timeout: Timeout | None = None


_trace_context: ContextVar[TraceContext | None] = ContextVar(
    "trace_context",
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


def get_trace_context() -> TraceContext | None:
    """Return correlation identifiers for the current asynchronous execution."""

    return _trace_context.get()


def cancellation_outcome() -> str:
    """Distinguish an expired response deadline from external cancellation."""

    context = get_trace_context()
    if (
        context is not None
        and context.timeout is not None
        and context.timeout.expired()
    ):
        return "timeout"
    return "cancelled"


def set_trace_context(value: TraceContext) -> Token[TraceContext | None]:
    """Set graph/MCP correlation identifiers and return the reset token."""

    return _trace_context.set(value)


def reset_trace_context(token: Token[TraceContext | None]) -> None:
    """Restore the previous graph/MCP correlation context."""

    _trace_context.reset(token)
