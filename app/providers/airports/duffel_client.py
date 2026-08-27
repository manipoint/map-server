"""Duffel airport and city-code lookup HTTP adapter."""

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.providers.airports.duffel_mapper import (
    map_duffel_airport_search,
)
from app.providers.airports.duffel_schemas import (
    DuffelPlaceSuggestionsResponse,
)
from app.providers.airports.schemas import (
    AirportSearchInput,
    AirportSearchResult,
)
from app.providers.duffel import (
    build_duffel_headers,
    build_duffel_url,
)


class DuffelAirportClient:
    """Resolve airport and city codes through Duffel Places."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        if settings.flight_provider != "duffel":
            raise ProviderConfigurationError(
                "Duffel airport provider is not configured"
            )

        if settings.duffel_api_key is None:
            raise ProviderConfigurationError(
                "Duffel airport provider credentials are missing"
            )

        self.http_client = http_client
        self.settings = settings
        self.api_key = settings.duffel_api_key

    @property
    def suggestions_url(self) -> str:
        """Return the Duffel Places Suggestions endpoint."""

        return build_duffel_url(
            base_url=self.settings.duffel_base_url,
            path="places/suggestions",
        )

    @property
    def headers(self) -> dict[str, str]:
        """Return authenticated Duffel request headers."""

        return build_duffel_headers(
            api_key=self.api_key,
            api_version=self.settings.duffel_api_version,
        )

    async def search_airports(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportSearchResult:
        """Search Duffel Places and return bounded normalized options."""

        try:
            response = await self.http_client.get(
                self.suggestions_url,
                headers=self.headers,
                params={
                    "query": request.query,
                },
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()

            parsed_response = DuffelPlaceSuggestionsResponse.model_validate(
                response.json()
            )

            return map_duffel_airport_search(
                request=request,
                response=parsed_response,
            )

        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "Duffel airport provider credentials were rejected"
                ) from error

            raise ProviderUnavailableError("Airport provider is unavailable") from error

        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Airport provider is unavailable") from error

        except (TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Airport provider returned an invalid response"
            ) from error
