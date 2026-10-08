"""WeatherAPI.com provider adapter."""

from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.common.time import UtcClock, utc_now
from app.config import Settings
from app.providers.weather.schemas import (
    CurrentWeather,
    DailyWeatherForecast,
    HourlyWeatherForecast,
    WeatherForecast,
)


class WeatherProvider(Protocol):
    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Return normalized current weather for a city."""

    async def get_forecast(
        self, *, city: str, start_date: date, end_date: date
    ) -> WeatherForecast:
        """Return available daily and hourly forecast for a date range."""


class WeatherApiClient:
    """WeatherAPI.com adapter that returns normalized weather data."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: UtcClock = utc_now,
    ) -> None:
        if settings.weather_api_key is None:
            raise ProviderConfigurationError("Weather provider is not configured")

        self.http_client = http_client
        self.settings = settings
        self.clock = clock

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Return normalized current weather for a non-empty city."""
        normalized_city = city.strip()
        if not normalized_city:
            raise ValueError("city must not be blank")

        try:
            response = await self.http_client.get(
                self.settings.weather_api_url,
                params={
                    "key": self.settings.weather_api_key.get_secret_value(),
                    "q": normalized_city,
                    "aqi": "no",
                },
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            location = payload["location"]
            current = payload["current"]

            return CurrentWeather(
                location=location["name"],
                country=location.get("country"),
                observed_at=datetime.fromtimestamp(
                    current["last_updated_epoch"],
                    tz=UTC,
                ),
                condition=current["condition"]["text"],
                temperature_c=current["temp_c"],
                feels_like_c=current.get("feelslike_c"),
                humidity_percent=current.get("humidity"),
                wind_kph=current.get("wind_kph"),
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "Weather provider credentials were rejected"
                ) from error
            raise ProviderUnavailableError("Weather provider is unavailable") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Weather provider is unavailable") from error

        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Weather provider returned an invalid response"
            ) from error

    async def get_forecast(
        self, *, city: str, start_date: date, end_date: date
    ) -> WeatherForecast:
        """Return provider-available hourly forecasts for the requested dates."""
        normalized_city = city.strip()
        if not normalized_city:
            raise ValueError("city must not be blank")
        if end_date < start_date:
            raise ValueError("end_date must not precede start_date")

        # WeatherAPI's forecast endpoint counts today as day one and accepts up
        # to 14 days; requested dates beyond actual account coverage are omitted.
        today = self.clock().astimezone(UTC).date()
        # Include one extra day for destinations behind UTC. For distant trips,
        # one day is sufficient to resolve the timezone; it is never trip weather.
        forecast_days = (
            1
            if (start_date - today).days > 14
            else min(max((end_date - today).days + 2, 1), 14)
        )
        try:
            response = await self.http_client.get(
                self.settings.weather_forecast_api_url,
                params={
                    "key": self.settings.weather_api_key.get_secret_value(),
                    "q": normalized_city,
                    "days": forecast_days,
                    "aqi": "no",
                    "alerts": "no",
                },
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            location = payload["location"]
            zone = ZoneInfo(location["tz_id"])
            provider_days = payload["forecast"]["forecastday"]
            normalized_days = []
            for entry in provider_days:
                forecast_date = date.fromisoformat(entry["date"])
                if not start_date <= forecast_date <= end_date:
                    continue
                day = entry["day"]
                hours = tuple(
                    HourlyWeatherForecast(
                        local_time=datetime.fromtimestamp(
                            hour["time_epoch"], tz=UTC
                        ).astimezone(zone),
                        condition=hour["condition"]["text"],
                        temperature_c=hour["temp_c"],
                        chance_of_rain_percent=hour.get("chance_of_rain"),
                        chance_of_snow_percent=hour.get("chance_of_snow"),
                        precipitation_mm=hour.get("precip_mm"),
                        snow_cm=hour.get("snow_cm"),
                    )
                    for hour in entry["hour"]
                )
                normalized_days.append(
                    DailyWeatherForecast(
                        date=forecast_date,
                        condition=day["condition"]["text"],
                        max_temperature_c=day["maxtemp_c"],
                        min_temperature_c=day["mintemp_c"],
                        total_precipitation_mm=day["totalprecip_mm"],
                        total_snow_cm=day.get("totalsnow_cm"),
                        hours=hours,
                    )
                )
            return WeatherForecast(
                location=location["name"],
                country=location.get("country"),
                time_zone=location["tz_id"],
                days=tuple(normalized_days),
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "Weather provider credentials were rejected"
                ) from error
            raise ProviderUnavailableError("Weather forecast is unavailable") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Weather forecast is unavailable") from error
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Weather provider returned an invalid forecast"
            ) from error
