"""WeatherAPI.com provider adapter."""

from datetime import UTC, datetime
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.providers.weather.schemas import CurrentWeather


class WeatherProvider(Protocol):
    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Return normalized current weather for a city."""


class WeatherApiClient:
    """WeatherAPI.com adapter that returns normalized weather data."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        if settings.weather_api_key is None:
            raise ProviderConfigurationError("Weather provider is not configured")

        self.http_client = http_client
        self.settings = settings

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
