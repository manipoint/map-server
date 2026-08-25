"""Tavily Search API transport adapter."""

import httpx
from pydantic import ValidationError

from app.common.exceptions import ProviderConfigurationError, ProviderUnavailableError
from app.config import Settings
from app.providers.places.tavily_schemas import (
    TavilyPlaceSearchRequest,
    TavilyPlaceSearchResponse,
)


class TavilySearchClient:
    """Execute cost-bounded Tavily place-discovery searches."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        if settings.places_provider != "tavily":
            raise ProviderConfigurationError("Tavily places provider is not configured")

        if settings.tavily_api_key is None:
            raise ProviderConfigurationError(
                "Tavily places provider credentials are missing"
            )

        self.http_client = http_client
        self.settings = settings
        self.api_key = settings.tavily_api_key

    @property
    def headers(self) -> dict[str, str]:
        """Return Tavily authentication and content headers."""

        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
        }

    async def search(
        self,
        *,
        request: TavilyPlaceSearchRequest,
    ) -> TavilyPlaceSearchResponse:
        """Execute one Tavily search and validate its bounded response."""

        try:
            response = await self.http_client.post(
                self.settings.tavily_search_api_url,
                headers=self.headers,
                json=request.model_dump(mode="json"),
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()
            return TavilyPlaceSearchResponse.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "Tavily places provider credentials were rejected"
                ) from error

            raise ProviderUnavailableError("Places provider is unavailable") from error

        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Places provider is unavailable") from error

        except (TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Places provider returned an invalid response"
            ) from error
