"""Curated destination catalogue and Home discovery domain values."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle
from app.domain.value_objects import CountryCode


class DiscoveryCollectionKind(StrEnum):
    """Honest label for the dynamic Home discovery collection."""

    FEATURED = "featured"
    TRENDING = "trending"


@dataclass(frozen=True, slots=True)
class DestinationCandidate:
    """Provider-independent destination content used by deterministic ranking."""

    id: UUID
    slug: str
    name: str
    country_name: str
    country_code: CountryCode
    summary: str
    image_url: str
    image_alt: str
    latitude: float
    longitude: float
    budget_tier: BudgetTier
    styles: tuple[TravelStyle, ...]
    interests: tuple[TravelInterest, ...]
    editorial_rank: int
    featured_rank: int | None
    popular_rank: int | None


@dataclass(frozen=True, slots=True)
class HomeDiscovery:
    """Complete low-cost Home payload before transport serialization."""

    personalization_ready: bool
    suggested: tuple[DestinationCandidate, ...]
    popular: tuple[DestinationCandidate, ...]
    spotlight_kind: DiscoveryCollectionKind
    spotlight: tuple[DestinationCandidate, ...]
