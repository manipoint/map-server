"""Bounded reads from the curated destination catalogue."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.destination import (
    Destination,
    DestinationInterest,
    DestinationStyle,
)
from app.domain.destinations import DestinationCandidate
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle

MAX_CATALOG_CANDIDATES = 100


class DestinationRepository:
    """Load published destination content without invoking external providers."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_published_catalog(
        self,
        *,
        limit: int = MAX_CATALOG_CANDIDATES,
    ) -> list[DestinationCandidate]:
        """Hydrate a bounded catalogue and its normalized tags in one SQL query."""

        if not 1 <= limit <= MAX_CATALOG_CANDIDATES:
            raise ValueError(f"limit must be between 1 and {MAX_CATALOG_CANDIDATES}")

        destination_ids = (
            select(Destination.id)
            .where(Destination.is_published.is_(True))
            .order_by(Destination.editorial_rank, Destination.id)
            .limit(limit)
            .subquery()
        )
        result = await self.session.execute(
            select(
                Destination,
                DestinationStyle.style,
                DestinationInterest.interest,
            )
            .join(destination_ids, destination_ids.c.id == Destination.id)
            .outerjoin(
                DestinationStyle,
                DestinationStyle.destination_id == Destination.id,
            )
            .outerjoin(
                DestinationInterest,
                DestinationInterest.destination_id == Destination.id,
            )
            .order_by(
                Destination.editorial_rank,
                Destination.id,
                DestinationStyle.style,
                DestinationInterest.interest,
            )
        )

        destinations: dict[UUID, Destination] = {}
        styles: dict[UUID, set[TravelStyle]] = {}
        interests: dict[UUID, set[TravelInterest]] = {}
        for destination, style, interest in result.all():
            destinations[destination.id] = destination
            styles.setdefault(destination.id, set())
            interests.setdefault(destination.id, set())
            if style is not None:
                styles[destination.id].add(TravelStyle(style))
            if interest is not None:
                interests[destination.id].add(TravelInterest(interest))

        return [
            self._to_candidate(
                destination=destination,
                styles=styles[destination.id],
                interests=interests[destination.id],
            )
            for destination in destinations.values()
        ]

    @staticmethod
    def _to_candidate(
        *,
        destination: Destination,
        styles: set[TravelStyle],
        interests: set[TravelInterest],
    ) -> DestinationCandidate:
        """Map persistence data into a transport-independent candidate."""

        return DestinationCandidate(
            id=destination.id,
            slug=destination.slug,
            name=destination.name,
            country_name=destination.country_name,
            country_code=destination.country_code,
            summary=destination.summary,
            image_url=destination.image_url,
            image_alt=destination.image_alt,
            latitude=destination.latitude,
            longitude=destination.longitude,
            budget_tier=BudgetTier(destination.budget_tier),
            styles=tuple(sorted(styles, key=lambda value: value.value)),
            interests=tuple(sorted(interests, key=lambda value: value.value)),
            editorial_rank=destination.editorial_rank,
            featured_rank=destination.featured_rank,
            popular_rank=destination.popular_rank,
        )
