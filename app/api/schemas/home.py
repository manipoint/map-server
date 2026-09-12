"""Public schemas for the low-cost personalized Home screen."""

from pydantic import BaseModel

from app.api.schemas.destinations import DestinationCardResponse
from app.domain.destinations import DiscoveryCollectionKind, HomeDiscovery


class DestinationCollectionResponse(BaseModel):
    """A collection whose label truthfully describes its source."""

    kind: DiscoveryCollectionKind
    items: list[DestinationCardResponse]


class HomeDiscoveryResponse(BaseModel):
    """Single response required to render Home discovery sections."""

    personalization_ready: bool
    suggested: list[DestinationCardResponse]
    suggested_local: list[DestinationCardResponse]
    suggested_international: list[DestinationCardResponse]
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
            suggested_local=[
                DestinationCardResponse.from_candidate(item)
                for item in discovery.suggested_local
            ],
            suggested_international=[
                DestinationCardResponse.from_candidate(item)
                for item in discovery.suggested_international
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
