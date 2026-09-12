"""Deterministic, provider-free Home destination discovery."""

from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.destinations import DestinationRepository
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.destinations import (
    DestinationCandidate,
    DestinationCollection,
    DiscoveryCollectionKind,
    HomeDiscovery,
)
from app.domain.preferences import RecommendationScope

HOME_SECTION_LIMIT: Final = 6


class HomeDiscoveryService:
    """Build Home sections using the same full-catalogue SQL ranking as View All."""

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

        async def suggested_for(
            scope: RecommendationScope,
        ) -> tuple[DestinationCandidate, ...]:
            if not preference.personalization_ready:
                return ()
            if (
                scope is not RecommendationScope.BOTH
                and preference.home_location is None
            ):
                return ()
            rows = await self.destinations.list_ranked(
                collection=DestinationCollection.SUGGESTED,
                preference=preference,
                scope=scope,
                limit=section_limit,
            )
            return tuple(row.destination for row in rows)

        suggested = await suggested_for(preference.recommendation_scope)
        suggested_local: tuple[DestinationCandidate, ...] = ()
        suggested_international: tuple[DestinationCandidate, ...] = ()
        if preference.recommendation_scope is RecommendationScope.LOCAL:
            suggested_local = suggested
        elif preference.recommendation_scope is RecommendationScope.INTERNATIONAL:
            suggested_international = suggested
        elif preference.home_location is not None:
            suggested_local = await suggested_for(RecommendationScope.LOCAL)
            suggested_international = await suggested_for(
                RecommendationScope.INTERNATIONAL
            )

        # AsyncSession is not concurrency-safe: bounded reads are sequential.
        popular = tuple(
            row.destination
            for row in await self.destinations.list_ranked(
                collection=DestinationCollection.POPULAR,
                limit=section_limit,
            )
        )
        featured = tuple(
            row.destination
            for row in await self.destinations.list_ranked(
                collection=DestinationCollection.FEATURED,
                limit=section_limit,
            )
        )

        return HomeDiscovery(
            personalization_ready=preference.personalization_ready,
            suggested=suggested,
            suggested_local=suggested_local,
            suggested_international=suggested_international,
            popular=popular,
            spotlight_kind=DiscoveryCollectionKind.FEATURED,
            spotlight=featured,
        )
