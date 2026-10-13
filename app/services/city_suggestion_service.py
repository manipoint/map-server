"""Country-scoped city suggestions using existing planning preferences."""

from app.database.repositories.destinations import DestinationRepository
from app.domain.city_suggestions import (
    MAX_CITY_SUGGESTIONS,
    CitySuggestion,
)
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.preferences import TravelInterest
from app.domain.value_objects import CountryCode


class CitySuggestionService:
    """Apply preference precedence without another user-preference query."""

    def __init__(self, *, repository: DestinationRepository) -> None:
        self.repository = repository

    async def suggest(
        self,
        *,
        country_code: CountryCode,
        planning: PlanningState,
        preferences: PlanningPreferences,
        limit: int = MAX_CITY_SUGGESTIONS,
    ) -> tuple[CitySuggestion, ...]:
        """Return ranked suggestions without selecting a city for the user."""
        if not 1 <= limit <= MAX_CITY_SUGGESTIONS:
            raise ValueError("limit must be between 1 and 3")
        interests = self._effective_interests(
            planning=planning, preferences=preferences
        )
        candidates = await self.repository.list_city_candidates(
            country_code=country_code,
            interests=interests,
            travel_styles=preferences.travel_styles,
            limit=limit,
        )
        effective_interests = set(interests)
        return tuple(
            CitySuggestion(
                destination_id=candidate.id,
                name=candidate.name,
                country_code=candidate.country_code,
                matched_interests=tuple(
                    sorted(
                        effective_interests.intersection(candidate.interests),
                        key=lambda interest: interest.value,
                    )
                ),
            )
            for candidate in candidates
        )

    @staticmethod
    def _effective_interests(
        *, planning: PlanningState, preferences: PlanningPreferences
    ) -> tuple[TravelInterest, ...]:
        """Explicit trip interests override profile defaults, including empty."""
        if not planning.interests_overridden:
            return preferences.interests
        supported = {interest.value: interest for interest in TravelInterest}
        return tuple(
            sorted(
                {
                    supported[value]
                    for value in planning.requirements.interests
                    if value in supported
                },
                key=lambda interest: interest.value,
            )
        )
