"""WeatherAPI deterministic location-search adapter."""

import httpx
from pydantic import TypeAdapter, ValidationError

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.providers.locations.schemas import ResolvedLocation
from app.providers.locations.weatherapi_schemas import (
    WeatherApiLocationCandidate,
)

_LOCATION_CANDIDATES_ADAPTER = TypeAdapter(list[WeatherApiLocationCandidate])


def build_display_name(
    candidate: WeatherApiLocationCandidate,
) -> str:
    """Build a compact location name without duplicate parts."""

    parts: list[str] = []
    seen_parts: set[str] = set()

    for value in (candidate.name, candidate.region, candidate.country):
        if value is None:
            continue
        normalized = value.strip()
        key = normalized.casefold()

        if normalized and key not in seen_parts:
            parts.append(normalized)
            seen_parts.add(key)

    return ", ".join(parts)


class WeatherApiLocationClient:
    """Resolve user location text through WeatherAPI search."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        if settings.weather_api_key is None:
            raise ProviderConfigurationError("Location provider is not configured")

        self.http_client = http_client
        self.settings = settings

    async def search_locations(
        self,
        *,
        query: str,
        max_results: int = 5,
    ) -> list[ResolvedLocation]:
        """Return ranked and deduplicated location candidates."""
        normalized_query = query.strip()
        if not 2 <= len(normalized_query) <= 120:
            raise ValueError("location query must contain between 2 and 120 characters")
        if not 1 <= max_results <= 10:
            raise ValueError("max_results must be between 1 and 10")

        try:
            response = await self.http_client.get(
                self.settings.weather_search_api_url,
                params={
                    "key": self.settings.weather_api_key.get_secret_value(),
                    "q": normalized_query,
                },
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()
            candidates = _LOCATION_CANDIDATES_ADAPTER.validate_python(response.json())
            locations: list[ResolvedLocation] = []
            seen_coordinates: set[tuple[float, float]] = set()

            for candidate in candidates:
                coordinates = (candidate.lat, candidate.lon)
                if coordinates in seen_coordinates:
                    continue

                locations.append(
                    ResolvedLocation(
                        query=normalized_query,
                        display_name=build_display_name(candidate)[:200],
                        latitude=candidate.lat,
                        longitude=candidate.lon,
                    )
                )
                seen_coordinates.add(coordinates)
                if len(locations) == max_results:
                    break
            return locations
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "Location provider credentials were rejected"
                ) from error

            raise ProviderUnavailableError(
                "Location provider is unavailable"
            ) from error

        except httpx.HTTPError as error:
            raise ProviderUnavailableError(
                "Location provider is unavailable"
            ) from error

        except (TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Location provider returned an invalid response"
            ) from error
