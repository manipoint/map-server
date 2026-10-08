"""Weather and variable-duration schedules survive validation and chat delivery."""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.domain.planning import PlanningState
from app.graph.planning_builder import validate_researched_itinerary
from app.graph.planning_schemas import ResearchedItinerary
from app.providers.weather.schemas import WeatherForecast
from app.services.planning_research_service import (
    PlanningResearch,
    PlanningResearchService,
)
from app.services.planning_weather import itinerary_weather_notes
from tests.unit.graph.test_planning_regressions import requirements, run_graph


def forecast():
    return WeatherForecast.model_validate(
        {
            "location": "Tokyo",
            "time_zone": "Asia/Tokyo",
            "days": [
                {
                    "date": "2099-11-07",
                    "condition": "Rain",
                    "max_temperature_c": 20,
                    "min_temperature_c": 10,
                    "total_precipitation_mm": 2,
                    "hours": [
                        {
                            "local_time": "2099-11-07T14:00:00+09:00",
                            "temperature_c": 18,
                            "condition": "Rain",
                            "chance_of_rain_percent": 90,
                            "chance_of_snow_percent": None,
                        }
                    ],
                }
            ],
        }
    )


def schedule():
    items = []
    for day in (1, 2):
        for kind, code, start, end in [
            ("meal", "breakfast", "08:00", "09:00"),
            ("transfer", "transfer", "09:00", "10:00"),
            ("activity", "photography", "10:00", "13:00"),
            ("activity", "hike", "13:00", "18:00"),
        ]:
            items.append(
                {
                    "day_number": day,
                    "item_type": kind,
                    "title": "Suggested stop",
                    "generic_activity": code,
                    "starts_at": f"2099-11-{day + 6:02}T{start}:00+09:00",
                    "ends_at": f"2099-11-{day + 6:02}T{end}:00+09:00",
                }
            )
    return {"summary": "Invented rain chance 5%", "items": items}


def research_data(**changes):
    return PlanningResearch(
        searched_at=datetime.now(UTC),
        time_zone="Asia/Tokyo",
        weather_requested=True,
        weather_forecast=forecast(),
        **changes,
    )


@pytest.mark.parametrize("language", ["en", "ur-Latn"])
def test_grounded_weather_and_schedule_reach_final_graph_output(language):
    research = AsyncMock()
    research.cache_key = Mock(return_value="same")
    research.research.return_value = research_data()
    result, gateway = run_graph(
        [
            {
                "intent": "plan",
                "language": language,
                "updates": {},
                "changed_fields": [],
            },
            schedule(),
        ],
        PlanningState(requirements=requirements()),
        research,
    )
    generated = result["generated_itinerary"]
    assert "90%" in result["assistant_response"]
    assert "5%" not in result["assistant_response"]
    assert "14:00 +0900" in result["assistant_response"]
    assert "?" in result["assistant_response"]
    assert len(generated.items) == 10
    assert generated.items[0].title == (
        "Nashta" if language == "ur-Latn" else "Breakfast"
    )
    assert generated.items[0].starts_at.hour == 8
    assert generated.items[2].ends_at.hour == 13
    assert (
        generated.items[3].ends_at - generated.items[3].starts_at
    ).total_seconds() == 5 * 3600
    assert generated.items[4].item_type == "note"
    assert generated.items[4].starts_at is None
    assert "90%" in result["planning"].itinerary["items"][4]["description"]
    assert gateway.generate.await_count == 2


@pytest.mark.parametrize("bad", ["missing", "overlap", "wrong_day", "wrong_offset"])
def test_invalid_model_schedule_cannot_reach_client(bad):
    data = schedule()
    item = data["items"][0]
    if bad == "missing":
        item["starts_at"] = item["ends_at"] = None
    elif bad == "overlap":
        data["items"][1]["starts_at"] = "2099-11-07T08:30:00+09:00"
    elif bad == "wrong_day":
        item["starts_at"] = "2099-11-06T08:00:00+09:00"
    else:
        item["starts_at"] = "2099-11-07T08:00:00+07:00"
        item["ends_at"] = "2099-11-07T09:00:00+07:00"
    with pytest.raises(ValueError):
        validate_researched_itinerary(
            ResearchedItinerary.model_validate(data),
            requirements=requirements(),
            research=research_data(),
        )


def test_model_missing_times_is_repaired_without_repeating_research():
    bad = schedule()
    bad["items"][0].update(starts_at=None, ends_at=None)
    research = AsyncMock()
    research.cache_key = Mock(return_value="same")
    research.research.return_value = research_data()
    result, gateway = run_graph(
        [
            {"intent": "plan", "updates": {}, "changed_fields": []},
            bad,
            schedule(),
        ],
        PlanningState(requirements=requirements()),
        research,
    )
    assert result["generated_itinerary"].items[0].starts_at.hour == 8
    assert gateway.generate.await_count == 3
    research.research.assert_awaited_once()


def test_partial_forecast_does_not_repeat_paid_research_on_pace_revision():
    cached = research_data()
    research = AsyncMock()
    research.cache_key = Mock(return_value="same")
    result, _ = run_graph(
        [
            {
                "intent": "revise",
                "updates": {"trip_pace": "relaxed"},
                "changed_fields": ["trip_pace"],
            },
            schedule(),
        ],
        PlanningState(
            requirements=requirements(),
            research_key="same",
            research=cached.model_dump(mode="json"),
        ),
        research,
    )
    research.research.assert_not_awaited()
    assert "2099-11-08" in result["assistant_response"]
    assert "unavailable" in result["assistant_response"]


@pytest.mark.parametrize(
    "failure", [TimeoutError(), ProviderUnavailableError("private error")]
)
def test_weather_failure_keeps_itinerary_research_usable(failure):
    client = AsyncMock()
    client.get_weather_forecast.side_effect = failure
    service = PlanningResearchService(
        client=client,
        places_available=False,
        hotels_available=False,
        round_trip_flights_available=False,
        weather_forecasts_available=True,
    )
    data = asyncio.run(service.research(requirements()))
    assert data.weather_requested and data.weather_forecast is None
    assert any("forecast unavailable" in warning for warning in data.warnings)
    assert "private error" not in str(data)


def test_daily_only_forecast_discloses_missing_hourly_data():
    raw = forecast().model_dump(mode="json")
    raw["days"][0]["hours"] = []
    notes = itinerary_weather_notes(
        start_date=date(2099, 11, 7),
        end_date=date(2099, 11, 8),
        forecast=WeatherForecast.model_validate(raw),
    )
    assert "Hourly forecast unavailable" in notes[0].description
    assert "Verified forecast unavailable" in notes[1].description


def test_weather_cancellation_propagates():
    client = AsyncMock()
    client.get_weather_forecast.side_effect = asyncio.CancelledError()
    service = PlanningResearchService(
        client=client,
        places_available=False,
        hotels_available=False,
        round_trip_flights_available=False,
        weather_forecasts_available=True,
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.research(requirements()))


def test_equal_hourly_chances_are_grouped_without_averaging_different_chances():
    raw = forecast().model_dump(mode="json")
    first = raw["days"][0]["hours"][0]
    raw["days"][0]["hours"] += [
        {**first, "local_time": "2099-11-07T15:00:00+09:00"},
        {
            **first,
            "local_time": "2099-11-07T16:00:00+09:00",
            "chance_of_rain_percent": 60,
        },
    ]
    notes = itinerary_weather_notes(
        start_date=date(2099, 11, 7),
        end_date=date(2099, 11, 8),
        forecast=WeatherForecast.model_validate(raw),
    )
    assert "14:00 +0900–16:00 +0900: rain 90%" in notes[0].description
    assert "16:00 +0900: rain 60%" in notes[0].description
    assert "combined range probability" in notes[0].description


def test_missing_hour_is_not_bridged_by_weather_window():
    raw = forecast().model_dump(mode="json")
    first = raw["days"][0]["hours"][0]
    raw["days"][0]["hours"].append({**first, "local_time": "2099-11-07T16:00:00+09:00"})
    notes = itinerary_weather_notes(
        start_date=date(2099, 11, 7),
        end_date=date(2099, 11, 8),
        forecast=WeatherForecast.model_validate(raw),
    )
    assert "14:00 +0900: rain 90%" in notes[0].description
    assert "16:00 +0900: rain 90%" in notes[0].description
    assert "15:00" not in notes[0].description
