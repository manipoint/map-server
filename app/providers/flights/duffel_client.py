"""Duffel flight provider HTTP adapter."""

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.config import Settings
from app.providers.flights.duffel_mapper import (
    build_duffel_offer_request,
    map_duffel_search_result,
)
from app.providers.flights.duffel_schemas import (
    DuffelOfferRequestResponse,
    DuffelOffersListResponse,
)
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""

    return datetime.now(UTC)


class DuffelFlightClient:
    """Search flights through Duffel and return normalized results."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if settings.flight_provider != "duffel":
            raise ProviderConfigurationError("Duffel flight provider is not configured")

        if settings.duffel_api_key is None:
            raise ProviderConfigurationError(
                "Duffel flight provider credentials are missing"
            )
        self.http_client = http_client
        self.settings = settings
        self.clock = clock

    @property
    def headers(self) -> dict[str, str]:
        """Return headers required by every Duffel request."""

        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Duffel-Version": self.settings.duffel_api_version,
            "Authorization": (
                f"Bearer {self.settings.duffel_api_key.get_secret_value()}"
            ),
        }

    @property
    def offer_requests_url(self) -> str:
        """Return the Duffel offer-request endpoint."""

        return f"{self.settings.duffel_base_url.rstrip('/')}/air/offer_requests"

    @property
    def offers_url(self) -> str:
        """Return the Duffel offers-list endpoint."""

        return f"{self.settings.duffel_base_url.rstrip('/')}/air/offers"

    async def _create_offer_request(
        self,
        *,
        request: FlightSearchInput,
    ) -> str:
        """Create a Duffel search and return its offer-request ID."""

        payload = build_duffel_offer_request(request)
        response = await self.http_client.post(
            self.offer_requests_url,
            headers=self.headers,
            params={
                "return_offers": "false",
                "supplier_timeout": self.settings.duffel_supplier_timeout_ms,
            },
            json=payload.model_dump(mode="json", exclude_none=True),
            timeout=self.settings.provider_timeout_seconds,
        )
        response.raise_for_status()

        parsed_response = DuffelOfferRequestResponse.model_validate(response.json())
        return parsed_response.data.id

    async def _list_offers(
        self,
        *,
        offer_request_id: str,
        request: FlightSearchInput,
    ) -> DuffelOffersListResponse:
        """Fetch a bounded, price-sorted page of Duffel offers."""

        params: dict[str, str | int] = {
            "offer_request_id": offer_request_id,
            "limit": request.max_results,
            "sort": "total_amount",
        }

        if request.nonstop_only:
            params["max_connections"] = 0

        response = await self.http_client.get(
            self.offers_url,
            headers=self.headers,
            params=params,
            timeout=self.settings.provider_timeout_seconds,
        )
        response.raise_for_status()

        return DuffelOffersListResponse.model_validate(response.json())

    async def search_flights(
        self,
        *,
        request: FlightSearchInput,
    ) -> FlightSearchResult:
        """Search Duffel and return a normalized bounded result."""
        try:
            offer_request_id = await self._create_offer_request(request=request)
            offers_response = await self._list_offers(
                offer_request_id=offer_request_id, request=request
            )
            return map_duffel_search_result(
                response=offers_response, request=request, searched_at=self.clock()
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "Duffel flight provider credentials were rejected"
                ) from error
            raise ProviderUnavailableError("Flight provider is unavailable") from error

        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Flight provider is unavailable") from error

        except (TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Flight provider returned an invalid response"
            ) from error
