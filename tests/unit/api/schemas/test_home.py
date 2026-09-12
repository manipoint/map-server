"""Tests for the Flutter Home discovery response contract."""

from uuid import uuid4

from app.api.schemas.home import HomeDiscoveryResponse
from app.domain.destinations import (
    DestinationCandidate,
    DestinationType,
    DiscoveryCollectionKind,
    HomeDiscovery,
    MediaAssetValue,
)
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle


def test_home_response_hides_internal_editorial_ranks() -> None:
    """Flutter should receive content and tags, not internal scoring controls."""

    candidate = DestinationCandidate(
        id=uuid4(),
        slug="hunza-pakistan",
        name="Hunza",
        destination_type=DestinationType.REGION,
        country_name="Pakistan",
        country_code="PK",
        summary="A mountain destination with trails and expansive valley views.",
        full_description="A complete mountain destination description for testing.",
        cover_image=MediaAssetValue(
            id=uuid4(),
            url="https://images.example.com/hunza.jpg",
            alt_text="Hunza valley",
            caption=None,
            width=None,
            height=None,
        ),
        latitude=36.3167,
        longitude=74.65,
        map_zoom=9,
        budget_tier=BudgetTier.MID_RANGE,
        styles=(TravelStyle.ADVENTURE, TravelStyle.NATURE),
        interests=(TravelInterest.HIKING,),
        editorial_rank=1,
        featured_rank=1,
        popular_rank=1,
    )
    discovery = HomeDiscovery(
        personalization_ready=True,
        suggested=(candidate,),
        suggested_local=(candidate,),
        suggested_international=(),
        popular=(candidate,),
        spotlight_kind=DiscoveryCollectionKind.FEATURED,
        spotlight=(candidate,),
    )

    payload = HomeDiscoveryResponse.from_discovery(discovery).model_dump(mode="json")

    assert payload["spotlight"]["kind"] == "featured"
    assert payload["suggested"][0]["country_code"] == "PK"
    assert payload["suggested"][0]["styles"] == ["adventure", "nature"]
    assert "editorial_rank" not in payload["suggested"][0]
    assert "popular_rank" not in payload["suggested"][0]
