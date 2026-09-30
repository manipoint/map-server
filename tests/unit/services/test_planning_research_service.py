"""Research tool isolation, concurrency and provider degradation."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.domain.trip_requirements import TripRequirements
from app.providers.places.schemas import PlaceSearchResult
from app.services.planning_research_service import PlanningResearchService


def complete_requirements(**changes):
    return TripRequirements.model_validate(
        {
            "destination": "Japan",
            "origin": "Lahore",
            "start_date": "2099-11-07",
            "duration_days": 5,
            "adults": 2,
            "minor_count": 0,
            "transport": "road",
            "needs_lodging": False,
            "budget_decision": "undecided",
            **changes,
        }
    )


def create_service(client, **changes):
    return PlanningResearchService(
        client=client,
        **{
            "places_available": True,
            "hotels_available": False,
            "round_trip_flights_available": False,
            **changes,
        },
    )


def place_result():
    return PlaceSearchResult.model_validate(
        {
            "status": "places_available",
            "searched_at": datetime.now(UTC),
            "location": {
                "query": "Japan",
                "display_name": "Japan",
                "latitude": 35,
                "longitude": 139,
            },
            "places": [
                {
                    "provider_place_id": "tokyo-1",
                    "name": "Temple",
                    "summary": "A temple visit",
                    "source_urls": ["https://example.com/place"],
                }
            ]
            * 2,
        }
    )


def test_research_compacts_and_deduplicates_verified_places():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    result = asyncio.run(create_service(client).research(complete_requirements()))
    assert len(result.evidence) == 1
    assert result.evidence[0].id == "place-1"
    client.search_hotels.assert_not_awaited()
    client.search_flights.assert_not_awaited()
    client.get_current_weather.assert_not_awaited()


def test_unavailable_roundtrip_and_hotel_providers_are_not_called():
    client = AsyncMock()
    client.search_places.return_value = place_result()
    result = asyncio.run(
        create_service(client).research(
            complete_requirements(
                transport="flight", cabin_class="economy", needs_lodging=True, rooms=1
            )
        )
    )
    assert len(result.warnings) == 2
    assert any("flights" in warning for warning in result.warnings)
    client.search_hotels.assert_not_awaited()
    client.search_flights.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [TimeoutError(), ProviderUnavailableError("private provider error")]
)
def test_expected_failure_is_safe_and_does_not_prevent_draft(failure):
    client = AsyncMock()
    client.search_places.side_effect = failure
    result = asyncio.run(create_service(client).research(complete_requirements()))
    assert not result.evidence
    assert len(result.warnings) == 1
    assert "private" not in str(result)


def test_research_cancellation_propagates():
    client = AsyncMock()
    client.search_places.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(create_service(client).research(complete_requirements()))


def test_independent_searches_start_concurrently():
    async def scenario():
        both_started = asyncio.Event()
        started = []

        async def search(*, request):
            started.append(request)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return place_result()

        client = AsyncMock()
        client.search_places.side_effect = search
        client.search_hotels.side_effect = search
        result = await create_service(client, hotels_available=True).research(
            complete_requirements(needs_lodging=True, rooms=1)
        )
        assert len(started) == 2
        return result

    asyncio.run(scenario())
