"""Google Places Text Search transport adapter."""

from typing import Final

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.common.time import UtcClock, utc_now
from app.config import Settings
from app.providers.places.google_mapper import (
    build_google_place_search,
    map_google_place_search_response,
)
from app.providers.places.google_schemas import (
    GooglePlaceTextSearchRequest,
    GooglePlaceTextSearchResponse,
)
from app.providers.places.schemas import (
    PlaceSearchResult,
    ResolvedPlaceSearch,
)

GOOGLE_PLACES_FIELD_MASK: Final = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.types",
        "places.primaryType",
        "places.googleMapsUri",
    )
)


class GooglePlacesClient:
    """Execute bounded Google Places Text Search requests."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: UtcClock = utc_now,
    ) -> None:
        if settings.places_provider != "google":
            raise ProviderConfigurationError("Google Places provider is not configured")

        if settings.google_places_api_key is None:
            raise ProviderConfigurationError(
                "Google Places provider credentials are missing"
            )

        self.http_client = http_client
        self.settings = settings
        self.api_key = settings.google_places_api_key
        self.clock = clock

    @property
    def headers(self) -> dict[str, str]:
        """Return Google authentication, content, and field-mask headers."""

        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key.get_secret_value(),
            "X-Goog-FieldMask": GOOGLE_PLACES_FIELD_MASK,
        }

    async def search(
        self,
        *,
        request: GooglePlaceTextSearchRequest,
    ) -> GooglePlaceTextSearchResponse:
        """Execute one Google Places search and validate its response."""

        try:
            response = await self.http_client.post(
                self.settings.google_places_text_search_url,
                headers=self.headers,
                json=request.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                ),
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()

            return GooglePlaceTextSearchResponse.model_validate(response.json())

        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "Google Places credentials were rejected"
                ) from error

            raise ProviderUnavailableError("Places provider is unavailable") from error

        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Places provider is unavailable") from error

        except (TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Places provider returned an invalid response"
            ) from error

    async def search_places(
        self,
        *,
        search: ResolvedPlaceSearch,
    ) -> PlaceSearchResult:
        """Search Google once and return normalized place options."""

        request = build_google_place_search(
            request=search.request,
            location=search.location,
        )

        response = await self.search(request=request)

        return map_google_place_search_response(
            response=response,
            search=search,
            searched_at=self.clock(),
        )
