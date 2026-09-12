"""Pure deterministic ranking for Home and destination collections."""

from typing import Final

from app.domain.destinations import DestinationCandidate
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    UserPreferenceSnapshot,
)
from app.domain.value_objects import CountryCode

STYLE_MATCH_SCORE: Final = 30
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


def rank_suggested(
    catalogue: list[DestinationCandidate],
    preference: UserPreferenceSnapshot,
    *,
    scope: RecommendationScope,
) -> tuple[DestinationCandidate, ...]:
    """Rank positive preference matches inside an explicit geographic scope."""

    if not preference.personalization_ready:
        return ()
    home_country_code = (
        preference.home_location.country_code
        if preference.home_location is not None
        else None
    )
    scored = [
        (preference_score(destination, preference), destination)
        for destination in catalogue
        if matches_scope(
            destination=destination,
            scope=scope,
            home_country_code=home_country_code,
        )
    ]
    return tuple(
        destination
        for score, destination in sorted(
            scored,
            key=lambda item: (-item[0], item[1].editorial_rank, item[1].slug),
        )
        if score > 0
    )


def rank_popular(
    catalogue: list[DestinationCandidate],
) -> tuple[DestinationCandidate, ...]:
    """Order destinations carrying an explicit popular rank."""

    return tuple(
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
        )
    )


def rank_featured(
    catalogue: list[DestinationCandidate],
) -> tuple[DestinationCandidate, ...]:
    """Order destinations carrying an explicit featured rank."""

    return tuple(
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
        )
    )


def matches_scope(
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


def preference_score(
    destination: DestinationCandidate,
    preference: UserPreferenceSnapshot,
) -> int:
    """Score all selected styles, interests, and the coarse budget distance."""

    style_matches = len(set(preference.travel_styles) & set(destination.styles))
    interest_matches = len(set(preference.interests) & set(destination.interests))
    score = style_matches * STYLE_MATCH_SCORE
    score += interest_matches * INTEREST_MATCH_SCORE

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
