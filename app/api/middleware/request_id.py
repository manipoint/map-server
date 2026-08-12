"""HTTP request-ID middleware."""

from uuid import UUID, uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.request_context import reset_request_id, set_request_id

REQUEST_ID_HEADER = "X-Request-ID"


def resolve_request_id(value: str | None) -> str:
    """Return a valid request ID or generate a new one."""

    if value:
        try:
            return str(UUID(value))
        except ValueError:
            pass

    return str(uuid4())


class RequestIdMiddleware:
    """Attach a validated request ID to every HTTP request and response."""

    def __init__(self, app: ASGIApp) -> None:
        """Store the next ASGI application in the middleware chain."""

        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Process one ASGI connection."""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        request_id = resolve_request_id(request_headers.get(REQUEST_ID_HEADER))

        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            """Add the request ID when the response starts."""

            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id

            await send(message)

        request_id_token = set_request_id(request_id)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(request_id_token)
