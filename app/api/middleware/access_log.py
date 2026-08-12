"""HTTP access logging middleware."""

import logging
from time import perf_counter_ns

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


def elapsed_milliseconds(started_at: int) -> float:
    """Return elapsed time in milliseconds."""

    elapsed_nanoseconds = perf_counter_ns() - started_at
    return round(elapsed_nanoseconds / 1_000_000, 3)


class AccessLogMiddleware:
    """Log the outcome and duration of every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        """Store the next ASGI application in the middleware chain."""

        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Process and log one ASGI HTTP request."""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = perf_counter_ns()
        status_code = 500

        async def send_with_status(message: Message) -> None:
            """Capture the response status before forwarding it."""

            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = message["status"]

            await send(message)

        request_id = scope.get("state", {}).get("request_id")
        log_context: dict[str, object] = {
            "http_method": scope["method"],
            "http_path": scope["path"],
        }
        if request_id is not None:
            log_context["request_id"] = request_id

        try:
            await self.app(scope, receive, send_with_status)
        except Exception:
            logger.exception(
                "HTTP request failed",
                extra={
                    **log_context,
                    "status_code": status_code,
                    "duration_ms": elapsed_milliseconds(started_at),
                },
            )
            raise

        logger.info(
            "HTTP request completed",
            extra={
                **log_context,
                "status_code": status_code,
                "duration_ms": elapsed_milliseconds(started_at),
            },
        )
