"""Public schemas for the low-cost personalized Home screen."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.domain.destinations import (
    DestinationCandidate,
    DiscoveryCollectionKind,
    HomeDiscovery,
)
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle
from app.domain.value_objects import CountryCode


class DestinationCardResponse(BaseModel):
    """Stable destination card content rendered directly by Flutter."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    name: str
    country_name: str
    country_code: CountryCode
    summary: str
    image_url: HttpUrl
    image_alt: str
    latitude: float
    longitude: float
    budget_tier: BudgetTier
    styles: list[TravelStyle]
    interests: list[TravelInterest]

    @classmethod
    def from_candidate(
        cls,
        candidate: DestinationCandidate,
    ) -> "DestinationCardResponse":
        """Exclude internal editorial ranks from the public contract."""

        return cls(
            id=candidate.id,
            slug=candidate.slug,
            name=candidate.name,
            country_name=candidate.country_name,
            country_code=candidate.country_code,
            summary=candidate.summary,
            image_url=candidate.image_url,
            image_alt=candidate.image_alt,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            budget_tier=candidate.budget_tier,
            styles=list(candidate.styles),
            interests=list(candidate.interests),
        )


class DestinationCollectionResponse(BaseModel):
    """A collection whose label truthfully describes its source."""

    kind: DiscoveryCollectionKind
    items: list[DestinationCardResponse]


class HomeDiscoveryResponse(BaseModel):
    """Single response required to render Phase 1 Home discovery."""

    personalization_ready: bool
    suggested: list[DestinationCardResponse]
    popular: list[DestinationCardResponse]
    spotlight: DestinationCollectionResponse

    @classmethod
    def from_discovery(cls, discovery: HomeDiscovery) -> "HomeDiscoveryResponse":
        """Serialize internal ranked collections without exposing scores."""

        return cls(
            personalization_ready=discovery.personalization_ready,
            suggested=[
                DestinationCardResponse.from_candidate(item)
                for item in discovery.suggested
            ],
            popular=[
                DestinationCardResponse.from_candidate(item)
                for item in discovery.popular
            ],
            spotlight=DestinationCollectionResponse(
                kind=discovery.spotlight_kind,
                items=[
                    DestinationCardResponse.from_candidate(item)
                    for item in discovery.spotlight
                ],
            ),
        )
