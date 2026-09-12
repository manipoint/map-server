"""Curated destination catalogue and discovery domain values."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle
from app.domain.value_objects import CountryCode


class DiscoveryCollectionKind(StrEnum):
    """Honest label for the dynamic Home discovery collection."""

    FEATURED = "featured"
    TRENDING = "trending"


class DestinationCollection(StrEnum):
    """Supported destination View All collections."""

    SUGGESTED = "suggested"
    POPULAR = "popular"
    FEATURED = "featured"


class DestinationType(StrEnum):
    """Geographic level represented by a destination landing page."""

    CITY = "city"
    REGION = "region"
    ISLAND = "island"
    COUNTRY = "country"


@dataclass(frozen=True, slots=True)
class MediaAssetValue:
    """Public image metadata independent of its storage provider."""

    id: UUID
    url: str
    alt_text: str
    caption: str | None
    width: int | None
    height: int | None


@dataclass(frozen=True, slots=True)
class DestinationCandidate:
    """Provider-independent destination content used by deterministic ranking."""

    id: UUID
    slug: str
    name: str
    destination_type: DestinationType
    country_name: str
    country_code: CountryCode
    summary: str
    full_description: str
    cover_image: MediaAssetValue
    latitude: float
    longitude: float
    map_zoom: int
    budget_tier: BudgetTier
    styles: tuple[TravelStyle, ...]
    interests: tuple[TravelInterest, ...]
    editorial_rank: int
    featured_rank: int | None
    popular_rank: int | None


@dataclass(frozen=True, slots=True)
class DestinationPlaceCandidate:
    """One curated place associated with a destination."""

    id: UUID
    slug: str
    name: str
    place_type: str
    summary: str
    full_description: str
    latitude: float
    longitude: float
    address: str | None
    sort_order: int
    is_featured: bool
    cover_image: MediaAssetValue | None


@dataclass(frozen=True, slots=True)
class DestinationDetail:
    """Complete bounded destination detail payload."""

    destination: DestinationCandidate
    gallery: tuple[MediaAssetValue, ...]
    places: tuple[DestinationPlaceCandidate, ...]
    places_next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class RankedDestination:
    """A hydrated card and its database ordering key for seek pagination."""

    destination: DestinationCandidate
    key: tuple[int, int, str]


@dataclass(frozen=True, slots=True)
class DestinationPlaceDetail:
    """Complete bounded detail for one curated destination place."""

    destination_slug: str
    place: DestinationPlaceCandidate
    gallery: tuple[MediaAssetValue, ...]


@dataclass(frozen=True, slots=True)
class HomeDiscovery:
    """Complete low-cost Home payload before transport serialization."""

    personalization_ready: bool
    suggested: tuple[DestinationCandidate, ...]
    suggested_local: tuple[DestinationCandidate, ...]
    suggested_international: tuple[DestinationCandidate, ...]
    popular: tuple[DestinationCandidate, ...]
    spotlight_kind: DiscoveryCollectionKind
    spotlight: tuple[DestinationCandidate, ...]
