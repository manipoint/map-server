"""Integration tests for the delete-conversation endpoint."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_conversation_service,
    get_current_principal,
)
from app.api.exception_handlers import (
    authentication_exception_handler,
    conversation_exception_handler,
)
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.conversations import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.database.models.user import User
from app.domain.errors import (
    ConversationError,
    ConversationInProgressError,
    ConversationNotFoundError,
)
from app.services.conversation_service import ConversationService


def create_delete_app(
    conversation_service: MagicMock,
    *,
    principal: MagicMock | None,
) -> FastAPI:
    """Create an isolated application containing conversation routes."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.add_exception_handler(
        ConversationError,
        conversation_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_conversation_service] = lambda: (
        conversation_service
    )

    if principal is not None:
        application.dependency_overrides[get_current_principal] = lambda: principal
    else:
        auth_service = MagicMock(spec=AuthService)
        application.dependency_overrides[get_auth_service] = lambda: auth_service

    return application


def create_principal() -> MagicMock:
    """Create an authenticated principal for route tests."""

    principal = MagicMock(spec=AuthenticatedPrincipal)
    principal.user = User(
        id=uuid4(),
        email="traveler@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    return principal


def test_delete_conversation_returns_empty_no_content_response() -> None:
    """Successful permanent deletion should return HTTP 204 with no body."""

    principal = create_principal()
    conversation_id = uuid4()
    conversation_service = MagicMock(spec=ConversationService)
    conversation_service.delete_conversation = AsyncMock(return_value=None)
    application = create_delete_app(conversation_service, principal=principal)

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/conversations/{conversation_id}")

    assert response.status_code == 204
    assert response.content == b""
    conversation_service.delete_conversation.assert_awaited_once_with(
        conversation_id=conversation_id,
        user_id=principal.user.id,
    )


def test_delete_conversation_returns_safe_not_found_for_missing_or_foreign() -> None:
    """Missing and differently owned conversations share a safe response."""

    principal = create_principal()
    conversation_service = MagicMock(spec=ConversationService)
    conversation_service.delete_conversation = AsyncMock(
        side_effect=ConversationNotFoundError("Conversation was not found")
    )
    application = create_delete_app(conversation_service, principal=principal)

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/conversations/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"
    assert response.json()["error"]["message"] == "Conversation was not found"


def test_delete_conversation_returns_conflict_when_planning_is_active() -> None:
    """Active planning cannot lose its persisted conversation mid-response."""

    principal = create_principal()
    conversation_service = MagicMock(spec=ConversationService)
    conversation_service.delete_conversation = AsyncMock(
        side_effect=ConversationInProgressError("Conversation planning is in progress")
    )
    application = create_delete_app(conversation_service, principal=principal)

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/conversations/{uuid4()}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conversation_in_progress"


def test_delete_conversation_rejects_invalid_uuid_before_service_call() -> None:
    """Invalid IDs must not reach the deletion service."""

    principal = create_principal()
    conversation_service = MagicMock(spec=ConversationService)
    conversation_service.delete_conversation = AsyncMock()
    application = create_delete_app(conversation_service, principal=principal)

    with TestClient(application) as client:
        response = client.delete("/api/v1/conversations/not-a-uuid")

    assert response.status_code == 422
    conversation_service.delete_conversation.assert_not_awaited()


def test_delete_conversation_requires_authentication() -> None:
    """Unauthenticated callers cannot reach permanent deletion."""

    conversation_service = MagicMock(spec=ConversationService)
    conversation_service.delete_conversation = AsyncMock()
    application = create_delete_app(conversation_service, principal=None)

    with TestClient(application) as client:
        response = client.delete(f"/api/v1/conversations/{uuid4()}")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_access_token"
    conversation_service.delete_conversation.assert_not_awaited()
