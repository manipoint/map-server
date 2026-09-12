"""Public schemas for destination collections, details, places, and media."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.domain.destinations import (
    DestinationCandidate,
    DestinationDetail,
    DestinationPlaceCandidate,
    DestinationPlaceDetail,
    DestinationType,
    MediaAssetValue,
)
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle
from app.domain.value_objects import CountryCode
from app.services.destination_catalogue_service import (
    DestinationPage,
    DestinationPlacePage,
)


class MediaAssetResponse(BaseModel):
    """Storage-provider-independent public image metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: HttpUrl
    alt_text: str
    caption: str | None
    width: int | None
    height: int | None

    @classmethod
    def from_value(cls, media: MediaAssetValue) -> "MediaAssetResponse":
        return cls.model_validate(media)


class DestinationCardResponse(BaseModel):
    """Stable destination card content rendered directly by Flutter."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    name: str
    destination_type: DestinationType
    country_name: str
    country_code: CountryCode
    summary: str
    cover_image: MediaAssetResponse
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
        """Exclude long detail copy and internal ranks from list cards."""

        return cls(
            id=candidate.id,
            slug=candidate.slug,
            name=candidate.name,
            destination_type=candidate.destination_type,
            country_name=candidate.country_name,
            country_code=candidate.country_code,
            summary=candidate.summary,
            cover_image=MediaAssetResponse.from_value(candidate.cover_image),
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            budget_tier=candidate.budget_tier,
            styles=list(candidate.styles),
            interests=list(candidate.interests),
        )


class DestinationListResponse(BaseModel):
    """One cursor-paginated View All response."""

    items: list[DestinationCardResponse]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: DestinationPage) -> "DestinationListResponse":
        return cls(
            items=[DestinationCardResponse.from_candidate(item) for item in page.items],
            next_cursor=page.next_cursor,
        )


class MapLocationResponse(BaseModel):
    """Map placement metadata for a destination or place."""

    latitude: float
    longitude: float
    map_zoom: int | None = None


class DestinationPlaceCardResponse(BaseModel):
    """Bounded place content used in previews and View All lists."""

    id: UUID
    slug: str
    name: str
    place_type: str
    summary: str
    location: MapLocationResponse
    address: str | None
    is_featured: bool
    cover_image: MediaAssetResponse | None

    @classmethod
    def from_candidate(
        cls,
        place: DestinationPlaceCandidate,
    ) -> "DestinationPlaceCardResponse":
        return cls(
            id=place.id,
            slug=place.slug,
            name=place.name,
            place_type=place.place_type,
            summary=place.summary,
            location=MapLocationResponse(
                latitude=place.latitude,
                longitude=place.longitude,
            ),
            address=place.address,
            is_featured=place.is_featured,
            cover_image=(
                MediaAssetResponse.from_value(place.cover_image)
                if place.cover_image is not None
                else None
            ),
        )


class DestinationPlaceListResponse(BaseModel):
    """One cursor-paginated destination-place response."""

    items: list[DestinationPlaceCardResponse]
    next_cursor: str | None

    @classmethod
    def from_page(
        cls,
        page: DestinationPlacePage,
    ) -> "DestinationPlaceListResponse":
        return cls(
            items=[
                DestinationPlaceCardResponse.from_candidate(item) for item in page.items
            ],
            next_cursor=page.next_cursor,
        )


class DestinationDetailResponse(BaseModel):
    """Destination overview, map centre, gallery, and place previews."""

    id: UUID
    slug: str
    name: str
    destination_type: DestinationType
    country_name: str
    country_code: CountryCode
    summary: str
    full_description: str
    location: MapLocationResponse
    budget_tier: BudgetTier
    styles: list[TravelStyle]
    interests: list[TravelInterest]
    gallery: list[MediaAssetResponse]
    places: list[DestinationPlaceCardResponse]
    places_next_cursor: str | None
    has_more_places: bool

    @classmethod
    def from_detail(cls, detail: DestinationDetail) -> "DestinationDetailResponse":
        destination = detail.destination
        return cls(
            id=destination.id,
            slug=destination.slug,
            name=destination.name,
            destination_type=destination.destination_type,
            country_name=destination.country_name,
            country_code=destination.country_code,
            summary=destination.summary,
            full_description=destination.full_description,
            location=MapLocationResponse(
                latitude=destination.latitude,
                longitude=destination.longitude,
                map_zoom=destination.map_zoom,
            ),
            budget_tier=destination.budget_tier,
            styles=list(destination.styles),
            interests=list(destination.interests),
            gallery=[MediaAssetResponse.from_value(item) for item in detail.gallery],
            places=[
                DestinationPlaceCardResponse.from_candidate(item)
                for item in detail.places
            ],
            places_next_cursor=detail.places_next_cursor,
            has_more_places=detail.places_next_cursor is not None,
        )


class DestinationPlaceDetailResponse(DestinationPlaceCardResponse):
    """Complete place copy and ordered image gallery."""

    destination_slug: str
    full_description: str
    gallery: list[MediaAssetResponse]

    @classmethod
    def from_detail(
        cls,
        detail: DestinationPlaceDetail,
    ) -> "DestinationPlaceDetailResponse":
        place = detail.place
        card = DestinationPlaceCardResponse.from_candidate(place)
        return cls(
            **card.model_dump(),
            destination_slug=detail.destination_slug,
            full_description=place.full_description,
            gallery=[MediaAssetResponse.from_value(item) for item in detail.gallery],
        )
