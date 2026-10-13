"""Typed city suggestions and deterministic reference ranking."""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.preferences import TravelInterest, TravelStyle
from app.domain.value_objects import CountryCode

MAX_CITY_SUGGESTIONS = 3


@dataclass(frozen=True, slots=True)
class CityCandidate:
    """Published city metadata supplied by the catalogue repository."""

    id: UUID
    slug: str
    name: str
    country_code: CountryCode
    interests: tuple[TravelInterest, ...]
    styles: tuple[TravelStyle, ...]
    editorial_rank: int


class CitySuggestion(BaseModel):
    """Compact suggestion; not a user-confirmed destination."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    destination_id: UUID
    name: str = Field(min_length=2, max_length=120)
    country_code: CountryCode
    matched_interests: tuple[TravelInterest, ...] = Field(
        default=(),
        max_length=10,
    )


def rank_city_candidates(
    candidates: Iterable[CityCandidate],
    *,
    country_code: CountryCode,
    interests: tuple[TravelInterest, ...],
    travel_styles: tuple[TravelStyle, ...] = (),
    limit: int = MAX_CITY_SUGGESTIONS,
) -> tuple[CitySuggestion, ...]:
    """Reference ordering for eligible, published catalogue cities. The production repository must apply this ordering in SQL before LIMIT.
    Do not load the entire catalogue into Python on the request path."""

    if not 1 <= limit <= MAX_CITY_SUGGESTIONS:
        raise ValueError("limit must be between 1 and 3")

    effective_interests = set(interests)
    effective_styles = set(travel_styles)
    eligible = [
        candidate for candidate in candidates if candidate.country_code == country_code
    ]

    def ordering(candidate: CityCandidate) -> tuple[int, int, int, str]:
        interest_matches = effective_interests.intersection(candidate.interests)
        style_matches = effective_styles.intersection(candidate.styles)

        return (
            -len(interest_matches),
            -len(style_matches),
            candidate.editorial_rank,
            candidate.slug,
        )

    ordered = sorted(eligible, key=ordering)
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
        for candidate in ordered[:limit]
    )
