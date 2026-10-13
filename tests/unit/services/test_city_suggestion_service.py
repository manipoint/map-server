import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.database.repositories.destinations import DestinationRepository
from app.domain.city_suggestions import CityCandidate
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.preferences import TravelInterest, TravelStyle
from app.domain.trip_requirements import TripRequirements
from app.services.city_suggestion_service import CitySuggestionService


@pytest.mark.parametrize(
    ("overridden", "trip_interests", "expected"),
    [
        (
            False,
            ("shopping",),
            (TravelInterest.HISTORY,),
        ),
        (
            True,
            ("shopping",),
            (TravelInterest.SHOPPING,),
        ),
        (
            True,
            (),
            (),
        ),
        (
            True,
            ("shopping", "architecture"),
            (TravelInterest.SHOPPING,),
        ),
    ],
)
def test_interest_precedence(
    overridden: bool,
    trip_interests: tuple[str, ...],
    expected: tuple[TravelInterest, ...],
) -> None:
    repository = Mock(spec=DestinationRepository)
    repository.list_city_candidates = AsyncMock(return_value=[])

    planning = PlanningState(
        requirements=TripRequirements(interests=trip_interests),
        interests_overridden=overridden,
    )
    preferences = PlanningPreferences(
        interests=(TravelInterest.HISTORY,),
        travel_styles=(TravelStyle.CULTURE,),
    )

    result = asyncio.run(
        CitySuggestionService(repository=repository).suggest(
            country_code="GB",
            planning=planning,
            preferences=preferences,
        )
    )

    assert result == ()
    repository.list_city_candidates.assert_awaited_once_with(
        country_code="GB",
        interests=expected,
        travel_styles=(TravelStyle.CULTURE,),
        limit=3,
    )
    assert planning.requirements.interests == trip_interests


def test_suggestions_preserve_repository_order_and_match_only_real_tags() -> None:
    candidates = [
        CityCandidate(
            id=uuid4(),
            slug="first-city",
            name="First City",
            country_code="GB",
            interests=(TravelInterest.HISTORY,),
            styles=(),
            editorial_rank=2,
        ),
        CityCandidate(
            id=uuid4(),
            slug="second-city",
            name="Second City",
            country_code="GB",
            interests=(TravelInterest.SHOPPING,),
            styles=(),
            editorial_rank=1,
        ),
    ]
    repository = Mock(spec=DestinationRepository)
    repository.list_city_candidates = AsyncMock(return_value=candidates)

    result = asyncio.run(
        CitySuggestionService(repository=repository).suggest(
            country_code="GB",
            planning=PlanningState(),
            preferences=PlanningPreferences(
                interests=(TravelInterest.HISTORY,),
            ),
        )
    )

    assert [item.destination_id for item in result] == [
        candidate.id for candidate in candidates
    ]
    assert result[0].matched_interests == (TravelInterest.HISTORY,)
    assert result[1].matched_interests == ()


@pytest.mark.parametrize("limit", [0, 4])
def test_invalid_limit_does_not_call_repository(limit: int) -> None:
    repository = Mock(spec=DestinationRepository)
    repository.list_city_candidates = AsyncMock()

    with pytest.raises(ValueError, match="between 1 and 3"):
        asyncio.run(
            CitySuggestionService(repository=repository).suggest(
                country_code="GB",
                planning=PlanningState(),
                preferences=PlanningPreferences(),
                limit=limit,
            )
        )

    repository.list_city_candidates.assert_not_awaited()
