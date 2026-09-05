"""Deterministic, provider-free Home destination discovery."""

from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.destinations import DestinationRepository
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.destinations import (
    DestinationCandidate,
    DiscoveryCollectionKind,
    HomeDiscovery,
)
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    UserPreferenceSnapshot,
)
from app.domain.value_objects import CountryCode

HOME_SECTION_LIMIT: Final = 6
STYLE_MATCH_SCORE: Final = 40
INTEREST_MATCH_SCORE: Final = 12
BUDGET_EXACT_SCORE: Final = 20
BUDGET_ADJACENT_SCORE: Final = 10
BUDGET_NEAR_SCORE: Final = 4

_BUDGET_ORDER: Final[dict[BudgetTier, int]] = {
    BudgetTier.BUDGET: 0,
    BudgetTier.MID_RANGE: 1,
    BudgetTier.PREMIUM: 2,
    BudgetTier.LUXURY: 3,
}


class HomeDiscoveryService:
    """Build stable Home sections from two bounded database reads."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        destination_repository: DestinationRepository | None = None,
        preference_repository: UserPreferenceRepository | None = None,
    ) -> None:
        self.destinations = destination_repository or DestinationRepository(session)
        self.preferences = preference_repository or UserPreferenceRepository(session)

    async def get_home(
        self,
        *,
        user_id: UUID,
        section_limit: int = HOME_SECTION_LIMIT,
    ) -> HomeDiscovery:
        """Return personalized and curated sections without variable API cost."""

        if not 1 <= section_limit <= HOME_SECTION_LIMIT:
            raise ValueError(
                f"section_limit must be between 1 and {HOME_SECTION_LIMIT}"
            )

        preference = await self.preferences.get_snapshot(user_id=user_id)
        catalogue = await self.destinations.list_published_catalog()

        suggested: tuple[DestinationCandidate, ...] = ()
        if preference.personalization_ready:
            scoped = [
                destination
                for destination in catalogue
                if self._matches_scope(
                    destination=destination,
                    scope=preference.recommendation_scope,
                    home_country_code=(
                        preference.home_location.country_code
                        if preference.home_location is not None
                        else None
                    ),
                )
            ]
            scored = [
                (self._preference_score(destination, preference), destination)
                for destination in scoped
            ]
            ranked = sorted(
                scored,
                key=lambda item: (
                    -item[0],
                    item[1].editorial_rank,
                    item[1].slug,
                ),
            )
            suggested = tuple(
                destination for score, destination in ranked if score > 0
            )[:section_limit]

        popular = tuple(
            sorted(
                (
                    destination
                    for destination in catalogue
                    if destination.popular_rank is not None
                ),
                key=lambda destination: (
                    destination.popular_rank,
                    destination.editorial_rank,
                    destination.slug,
                ),
            )[:section_limit]
        )
        featured = tuple(
            sorted(
                (
                    destination
                    for destination in catalogue
                    if destination.featured_rank is not None
                ),
                key=lambda destination: (
                    destination.featured_rank,
                    destination.editorial_rank,
                    destination.slug,
                ),
            )[:section_limit]
        )

        return HomeDiscovery(
            personalization_ready=preference.personalization_ready,
            suggested=suggested,
            popular=popular,
            spotlight_kind=DiscoveryCollectionKind.FEATURED,
            spotlight=featured,
        )

    @staticmethod
    def _matches_scope(
        *,
        destination: DestinationCandidate,
        scope: RecommendationScope,
        home_country_code: CountryCode | None,
    ) -> bool:
        """Apply explicit geographic intent without inferring it from budget."""

        if scope is RecommendationScope.BOTH:
            return True
        if home_country_code is None:
            return False
        is_local = destination.country_code == home_country_code
        return is_local if scope is RecommendationScope.LOCAL else not is_local

    @staticmethod
    def _preference_score(
        destination: DestinationCandidate,
        preference: UserPreferenceSnapshot,
    ) -> int:
        """Score one candidate using fixed, explainable product weights."""

        score = 0
        if preference.travel_style in destination.styles:
            score += STYLE_MATCH_SCORE
        score += len(set(preference.interests) & set(destination.interests)) * (
            INTEREST_MATCH_SCORE
        )

        if preference.budget_tier is not None:
            distance = abs(
                _BUDGET_ORDER[preference.budget_tier]
                - _BUDGET_ORDER[destination.budget_tier]
            )
            score += {
                0: BUDGET_EXACT_SCORE,
                1: BUDGET_ADJACENT_SCORE,
                2: BUDGET_NEAR_SCORE,
            }.get(distance, 0)
        return score
