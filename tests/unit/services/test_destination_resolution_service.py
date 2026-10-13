import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.database.repositories.destinations import DestinationRepository
from app.domain.city_suggestions import CityCandidate, CitySuggestion
from app.domain.destination_selection import (
    CountryMatch,
    CountryRecognition,
    PendingDestinationSelection,
)
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.preferences import TravelInterest
from app.services.country_recognition_service import CountryRecognitionService
from app.services.destination_resolution_service import DestinationResolutionService


def setup_service():
    repository = Mock(spec=DestinationRepository)
    repository.list_city_candidates = AsyncMock(return_value=[])
    repository.get_published_city = AsyncMock(return_value=None)
    repository.find_published_cities = AsyncMock(return_value=())

    service = DestinationResolutionService(
        repository=repository,
        country_recognition=CountryRecognitionService(),
    )
    return service, repository


def pending_city() -> PendingDestinationSelection:
    return PendingDestinationSelection(
        country=CountryMatch(
            country_code="GB",
            country_name="United Kingdom",
            query="UK",
        ),
        options=(
            CitySuggestion(
                destination_id=uuid4(),
                name="London",
                country_code="GB",
            ),
        ),
    )


def test_country_preparation_retrieves_interest_ranked_options():
    service, repository = setup_service()
    city_id = uuid4()
    repository.list_city_candidates.return_value = [
        CityCandidate(
            id=city_id,
            slug="london-united-kingdom",
            name="London",
            country_code="GB",
            interests=(TravelInterest.HISTORY,),
            styles=(),
            editorial_rank=1,
        )
    ]

    recognition, pending = asyncio.run(
        service.prepare_country(
            query="UK",
            planning=PlanningState(),
            preferences=PlanningPreferences(
                interests=(TravelInterest.HISTORY,),
            ),
        )
    )

    assert recognition.status == "exact"
    assert pending is not None
    assert pending.options[0].destination_id == city_id
    assert pending.options[0].matched_interests == (TravelInterest.HISTORY,)
    repository.list_city_candidates.assert_awaited_once_with(
        country_code="GB",
        interests=(TravelInterest.HISTORY,),
        travel_styles=(),
        limit=3,
    )


@pytest.mark.parametrize("query", ["Korea", "zzzzzzzzzz"])
def test_unresolved_country_does_not_query_city_suggestions(query):
    service, repository = setup_service()

    _, pending = asyncio.run(
        service.prepare_country(
            query=query,
            planning=PlanningState(),
            preferences=PlanningPreferences(),
        )
    )

    assert pending is None
    repository.list_city_candidates.assert_not_awaited()


def test_typo_proposal_remains_unconfirmed():
    service, _ = setup_service()

    recognition, pending = asyncio.run(
        service.prepare_country(
            query="turky",
            allow_typo=True,
            planning=PlanningState(),
            preferences=PlanningPreferences(),
        )
    )

    assert recognition.status == "confirmation_required"
    assert pending is not None
    assert pending.country.requires_confirmation is True


def test_country_confirmation_rejects_an_unoffered_code():
    service, repository = setup_service()
    recognition = CountryRecognition(
        status="confirmation_required",
        matches=(
            CountryMatch(
                country_code="TR",
                country_name="Turkey",
                query="turky",
                requires_confirmation=True,
            ),
        ),
    )

    with pytest.raises(ValueError, match="not offered"):
        asyncio.run(
            service.confirm_country(
                recognition=recognition,
                selected_code="JP",
                planning=PlanningState(),
                preferences=PlanningPreferences(),
            )
        )

    repository.list_city_candidates.assert_not_awaited()


def test_city_selection_uses_current_database_result():
    service, repository = setup_service()
    pending = pending_city()
    offered_id = pending.options[0].destination_id
    fresh = CitySuggestion(
        destination_id=offered_id,
        name="Updated London Name",
        country_code="GB",
    )
    repository.get_published_city.return_value = fresh

    selected = asyncio.run(
        service.select_offered_city(
            pending=pending,
            destination_id=offered_id,
        )
    )

    assert selected == fresh
    repository.get_published_city.assert_awaited_once_with(
        destination_id=offered_id,
        country_code="GB",
    )


def test_stale_city_is_not_accepted():
    service, _ = setup_service()
    pending = pending_city()

    selected = asyncio.run(
        service.select_offered_city(
            pending=pending,
            destination_id=pending.options[0].destination_id,
        )
    )

    assert selected is None


def test_city_selection_rejects_an_unoffered_id():
    service, repository = setup_service()

    with pytest.raises(ValueError, match="not offered"):
        asyncio.run(
            service.select_offered_city(
                pending=pending_city(),
                destination_id=uuid4(),
            )
        )

    repository.get_published_city.assert_not_awaited()


def test_free_text_city_lookup_is_country_scoped():
    service, repository = setup_service()

    result = asyncio.run(
        service.resolve_city_name(
            pending=pending_city(),
            query="Another City",
        )
    )

    assert result == ()
    repository.find_published_cities.assert_awaited_once_with(
        query="Another City",
        country_code="GB",
    )


def test_country_confirmation_clears_confirmation_flag():
    service, repository = setup_service()
    recognition = CountryRecognition(
        status="confirmation_required",
        matches=(
            CountryMatch(
                country_code="TR",
                country_name="Turkey",
                query="turky",
                requires_confirmation=True,
            ),
        ),
    )

    pending = asyncio.run(
        service.confirm_country(
            recognition=recognition,
            selected_code="TR",
            planning=PlanningState(),
            preferences=PlanningPreferences(),
        )
    )

    assert pending.country.country_code == "TR"
    assert pending.country.requires_confirmation is False
    assert recognition.matches[0].requires_confirmation is True
    repository.list_city_candidates.assert_awaited_once_with(
        country_code="TR",
        interests=(),
        travel_styles=(),
        limit=3,
    )
