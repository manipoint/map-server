"""Integration tests for authenticated preference routes."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_auth_service,
    get_current_principal,
    get_user_preference_service,
)
from app.api.exception_handlers import authentication_exception_handler
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.routes.user_preferences import router
from app.auth.exceptions import AuthenticationError
from app.auth.service import AuthenticatedPrincipal, AuthService
from app.database.models.user import User
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
    UserPreferenceSnapshot,
)
from app.services.user_preference_service import UserPreferenceService


def create_app(
    service: MagicMock,
    *,
    authenticated: bool = True,
) -> tuple[FastAPI, MagicMock | None]:
    """Create one isolated preference API with optional authentication."""

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(
        AuthenticationError,
        authentication_exception_handler,
    )
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_user_preference_service] = lambda: service
    principal = None
    if authenticated:
        user = User(
            id=uuid4(),
            email="traveler@example.com",
            password_hash="stored-hash",
            status="active",
        )
        principal = MagicMock(spec=AuthenticatedPrincipal)
        principal.user = user
        application.dependency_overrides[get_current_principal] = lambda: principal
    else:
        auth_service = MagicMock(spec=AuthService)
        application.dependency_overrides[get_auth_service] = lambda: auth_service
    return application, principal


def create_snapshot(*, user_id, completed: bool) -> UserPreferenceSnapshot:
    """Create a response snapshot for HTTP contract tests."""

    now = datetime(2026, 9, 5, 9, 0, tzinfo=UTC) if completed else None
    return UserPreferenceSnapshot(
        user_id=user_id,
        travel_style=TravelStyle.NATURE if completed else None,
        interests=(TravelInterest.HIKING, TravelInterest.HISTORY) if completed else (),
        budget_tier=BudgetTier.MID_RANGE if completed else None,
        trip_pace=TripPace.BALANCED if completed else None,
        recommendation_scope=RecommendationScope.BOTH,
        home_location=None,
        onboarding_completed_at=now,
        created_at=now,
        updated_at=now,
    )


def test_get_returns_stable_defaults_for_new_user() -> None:
    """Flutter should route new accounts without interpreting a 404."""

    service = MagicMock(spec=UserPreferenceService)
    service.get_preferences = AsyncMock()
    application, principal = create_app(service)
    assert principal is not None
    service.get_preferences.return_value = create_snapshot(
        user_id=principal.user.id,
        completed=False,
    )

    with TestClient(application) as client:
        response = client.get("/api/v1/users/me/preferences")

    assert response.status_code == 200
    assert response.json() == {
        "travel_style": None,
        "interests": [],
        "budget_tier": None,
        "trip_pace": None,
        "recommendation_scope": "both",
        "home_location": None,
        "onboarding_completed": False,
        "personalization_ready": False,
        "onboarding_completed_at": None,
        "created_at": None,
        "updated_at": None,
    }
    service.get_preferences.assert_awaited_once_with(user_id=principal.user.id)


def test_put_validates_and_returns_completed_preferences() -> None:
    """One final onboarding request should persist all choices atomically."""

    service = MagicMock(spec=UserPreferenceService)
    service.complete_onboarding = AsyncMock()
    application, principal = create_app(service)
    assert principal is not None
    service.complete_onboarding.return_value = create_snapshot(
        user_id=principal.user.id,
        completed=True,
    )

    with TestClient(application) as client:
        response = client.put(
            "/api/v1/users/me/preferences",
            json={
                "travel_style": "nature",
                "interests": ["hiking", "history"],
                "budget_tier": "mid_range",
                "trip_pace": "balanced",
                "recommendation_scope": "both",
                "home_location": None,
            },
        )

    assert response.status_code == 200
    assert response.json()["onboarding_completed"] is True
    assert response.json()["personalization_ready"] is True
    assert response.json()["interests"] == ["hiking", "history"]
    service.complete_onboarding.assert_awaited_once()
    assert service.complete_onboarding.await_args.kwargs["user_id"] == principal.user.id


def test_put_rejects_local_scope_without_home_before_service() -> None:
    """Invalid locality requests must not spend a database transaction."""

    service = MagicMock(spec=UserPreferenceService)
    service.complete_onboarding = AsyncMock()
    application, _ = create_app(service)

    with TestClient(application) as client:
        response = client.put(
            "/api/v1/users/me/preferences",
            json={
                "travel_style": "nature",
                "interests": ["hiking"],
                "budget_tier": "budget",
                "trip_pace": "relaxed",
                "recommendation_scope": "local",
                "home_location": None,
            },
        )

    assert response.status_code == 422
    service.complete_onboarding.assert_not_awaited()


def test_skip_marks_onboarding_complete_without_fake_personalization() -> None:
    """Skip should not invent interests to make the profile look complete."""

    service = MagicMock(spec=UserPreferenceService)
    service.skip_onboarding = AsyncMock()
    application, principal = create_app(service)
    assert principal is not None
    snapshot = create_snapshot(user_id=principal.user.id, completed=False)
    snapshot = replace(
        snapshot,
        onboarding_completed_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
    )
    service.skip_onboarding.return_value = snapshot

    with TestClient(application) as client:
        response = client.post("/api/v1/users/me/onboarding/skip")

    assert response.status_code == 200
    assert response.json()["onboarding_completed"] is True
    assert response.json()["personalization_ready"] is False


def test_preferences_require_authentication() -> None:
    """Preference data must never be exposed without a valid session."""

    service = MagicMock(spec=UserPreferenceService)
    service.get_preferences = AsyncMock()
    application, _ = create_app(service, authenticated=False)

    with TestClient(application) as client:
        response = client.get("/api/v1/users/me/preferences")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"
    service.get_preferences.assert_not_awaited()
