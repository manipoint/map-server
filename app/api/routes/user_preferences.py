"""Authenticated onboarding and user-preference routes."""

from fastapi import APIRouter

from app.api.dependencies import CurrentPrincipal, UserPreferenceServiceDependency
from app.api.schemas.user_preferences import (
    UserPreferenceResponse,
    UserPreferenceUpdateRequest,
)

router = APIRouter(prefix="/users/me", tags=["user preferences"])


@router.get(
    "/preferences",
    response_model=UserPreferenceResponse,
    summary="Get current user's travel preferences",
)
async def get_user_preferences(
    principal: CurrentPrincipal,
    service: UserPreferenceServiceDependency,
) -> UserPreferenceResponse:
    """Return stable defaults or the caller's saved onboarding choices."""

    snapshot = await service.get_preferences(user_id=principal.user.id)
    return UserPreferenceResponse.from_snapshot(snapshot)


@router.put(
    "/preferences",
    response_model=UserPreferenceResponse,
    summary="Complete or replace current user's travel preferences",
)
async def replace_user_preferences(
    payload: UserPreferenceUpdateRequest,
    principal: CurrentPrincipal,
    service: UserPreferenceServiceDependency,
) -> UserPreferenceResponse:
    """Atomically replace selections without invoking an LLM or provider."""

    snapshot = await service.complete_onboarding(
        user_id=principal.user.id,
        travel_styles=payload.travel_styles,
        interests=payload.interests,
        budget_tier=payload.budget_tier,
        trip_pace=payload.trip_pace,
        recommendation_scope=payload.recommendation_scope,
        home_location=payload.home_location,
    )
    return UserPreferenceResponse.from_snapshot(snapshot)


@router.post(
    "/onboarding/skip",
    response_model=UserPreferenceResponse,
    summary="Skip preference onboarding",
)
async def skip_user_onboarding(
    principal: CurrentPrincipal,
    service: UserPreferenceServiceDependency,
) -> UserPreferenceResponse:
    """Mark onboarding complete without fabricating preference selections."""

    snapshot = await service.skip_onboarding(user_id=principal.user.id)
    return UserPreferenceResponse.from_snapshot(snapshot)
