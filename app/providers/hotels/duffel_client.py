"""Duffel hotel provider HTTP adapter."""

import logging

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.common.time import UtcClock, utc_now
from app.config import Settings
from app.providers.duffel import build_duffel_headers, build_duffel_url
from app.providers.hotels.duffel_mapper import (
    build_duffel_stay_search,
    map_duffel_stay_search_response,
)
from app.providers.hotels.duffel_schemas import DuffelStaySearchResponse
from app.providers.hotels.schemas import (
    HotelSearchResult,
    ResolvedHotelSearch,
)

logger = logging.getLogger(__name__)


class DuffelHotelClient:
    """Search hotels through Duffel and return normalized results."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: UtcClock = utc_now,
    ) -> None:
        if settings.hotel_provider != "duffel":
            raise ProviderConfigurationError("Duffel hotel provider is not configured")
        if settings.duffel_api_key is None:
            raise ProviderConfigurationError(
                "Duffel hotel provider credentials are missing"
            )

        self.http_client = http_client
        self.settings = settings
        self.api_key = settings.duffel_api_key
        self.clock = clock

    @property
    def headers(self) -> dict[str, str]:
        """Return headers required by Duffel Stays."""

        return build_duffel_headers(
            api_key=self.api_key,
            api_version=self.settings.duffel_api_version,
        )

    @property
    def search_url(self) -> str:
        """Return the Duffel Stays search endpoint."""

        return build_duffel_url(
            base_url=self.settings.duffel_base_url,
            path="stays/search",
        )

    async def search_hotels(
        self,
        *,
        search: ResolvedHotelSearch,
    ) -> HotelSearchResult:
        """Search Duffel Stays and return normalized availability."""

        try:
            payload = build_duffel_stay_search(search)
            response = await self.http_client.post(
                self.search_url,
                headers=self.headers,
                json=payload.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()

            parsed_response = DuffelStaySearchResponse.model_validate(response.json())

            return map_duffel_stay_search_response(
                response=parsed_response,
                search=search,
                searched_at=self.clock(),
            )
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code

            logger.warning(
                "Duffel hotel request failed",
                extra={
                    "status_code": status_code,
                    "duffel_request_id": error.response.headers.get("x-request-id"),
                },
            )

            if status_code == 401:
                raise ProviderConfigurationError(
                    "Duffel hotel provider credentials were rejected"
                ) from error

            if status_code == 403:
                raise ProviderConfigurationError(
                    "Duffel Stays access was denied"
                ) from error

            raise ProviderUnavailableError("Hotel provider is unavailable") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Hotel provider is unavailable") from error

        except (TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Hotel provider returned an invalid response"
            ) from error
