"""Forecast boundary, partial coverage and missing-data tests without network."""

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from app.common.exceptions import ProviderUnavailableError
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.providers.weather.client import WeatherApiClient
from app.providers.weather.schemas import WeatherForecast
from tests.unit.providers.weather.test_client import create_settings


def payload():
    return {
        "location": {"name": "Tokyo", "tz_id": "Asia/Tokyo"},
        "forecast": {
            "forecastday": [
                {
                    "date": "2026-10-08",
                    "day": {
                        "condition": {"text": "Rain"},
                        "maxtemp_c": 20,
                        "mintemp_c": 10,
                        "totalprecip_mm": 2,
                    },
                    "hour": [
                        {
                            "time_epoch": 1791435600,
                            "temp_c": 18,
                            "condition": {"text": "Rain"},
                            "chance_of_rain": 90,
                            "chance_of_snow": 0,
                        }
                    ],
                }
            ]
        },
    }


def fetch(data, *, start=date(2026, 10, 8), end=date(2026, 10, 9), status=200):
    requests = []

    async def exercise():
        def handler(request):
            requests.append(request)
            return httpx.Response(status, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = WeatherApiClient(
                http_client=http,
                settings=create_settings(),
                clock=lambda: datetime(2026, 10, 8, tzinfo=UTC),
            )
            # Exercise input validation, MCP serialization and output validation too.
            client = TravelMcpClient(create_mcp_server(weather_provider=provider))
            return await client.get_weather_forecast(
                city="Tokyo", start_date=start, end_date=end
            )

    return asyncio.run(exercise()), requests


def test_thirty_day_request_returns_partial_coverage_through_real_mcp():
    forecast, requests = fetch(payload(), end=date(2026, 11, 6))
    assert len(forecast.days) == 1
    assert (
        forecast.days[0].hours[0].local_time.isoformat() == "2026-10-08T14:00:00+09:00"
    )
    assert requests[0].url.params["days"] == "14"
    assert len(requests) == 1


def test_distant_trip_resolves_timezone_without_reusing_todays_weather():
    forecast, requests = fetch(
        payload(), start=date(2099, 11, 7), end=date(2099, 11, 8)
    )
    assert forecast.days == ()
    assert forecast.time_zone == "Asia/Tokyo"
    assert requests[0].url.params["days"] == "1"


def test_missing_probabilities_are_unknown_not_zero():
    data = payload()
    hour = data["forecast"]["forecastday"][0]["hour"][0]
    del hour["chance_of_rain"]
    del hour["chance_of_snow"]
    forecast, _ = fetch(data)
    assert forecast.days[0].hours[0].chance_of_rain_percent is None
    assert forecast.days[0].hours[0].chance_of_snow_percent is None
    assert forecast.days[0].total_snow_cm is None


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_date",
        "duplicate_hour",
        "duplicate_day",
        "bad_chance",
        "invalid_zone",
        "bad_temperature",
    ],
)
def test_malformed_provider_data_is_rejected(mutation):
    data = payload()
    day = data["forecast"]["forecastday"][0]
    if mutation == "wrong_date":
        day["hour"][0]["time_epoch"] += 86400
    elif mutation == "duplicate_hour":
        day["hour"].append(deepcopy(day["hour"][0]))
    elif mutation == "duplicate_day":
        data["forecast"]["forecastday"].append(deepcopy(day))
    elif mutation == "bad_chance":
        day["hour"][0]["chance_of_rain"] = 101
    elif mutation == "invalid_zone":
        data["location"]["tz_id"] = "not/a/timezone"
        day["hour"] = []
    else:
        day["day"]["mintemp_c"] = 30
    with pytest.raises(ProviderUnavailableError):
        fetch(data)


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_provider_errors_are_safe_through_mcp(status):
    with pytest.raises(ProviderUnavailableError) as error:
        fetch({"private": "provider diagnostic"}, status=status)
    assert "diagnostic" not in str(error.value)


def test_forecast_schema_rejects_naive_and_wrong_offset_timestamps():
    forecast, _ = fetch(payload())
    for local_time in ["2026-10-08T14:00:00", "2026-10-08T14:00:00Z"]:
        raw = forecast.model_dump(mode="json")
        raw["days"][0]["hours"][0]["local_time"] = local_time
        with pytest.raises(ValidationError):
            WeatherForecast.model_validate(raw)


def test_dst_day_accepts_twenty_five_distinct_hours():
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 4, tzinfo=UTC)
    forecast = WeatherForecast.model_validate(
        {
            "location": "New York",
            "time_zone": "America/New_York",
            "days": [
                {
                    "date": "2026-11-01",
                    "condition": "Cloudy",
                    "max_temperature_c": 20,
                    "min_temperature_c": 10,
                    "total_precipitation_mm": 0,
                    "hours": [
                        {
                            "local_time": (first + timedelta(hours=i)).astimezone(zone),
                            "condition": "Cloudy",
                            "temperature_c": 15,
                        }
                        for i in range(25)
                    ],
                }
            ],
        }
    )
    assert len(forecast.days[0].hours) == 25
    assert (
        forecast.days[0].hours[1].local_time.hour
        == forecast.days[0].hours[2].local_time.hour
    )


def test_invalid_request_does_not_call_mcp():
    server = AsyncMock()
    client = TravelMcpClient(server)
    with pytest.raises(ValidationError):
        asyncio.run(
            client.get_weather_forecast(
                city="Tokyo", start_date=date(2026, 10, 8), end_date=date(2026, 10, 7)
            )
        )
    server.call_tool.assert_not_awaited()
