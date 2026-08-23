"""Duffel flight provider HTTP adapter."""

from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.common.exceptions import ProviderConfigurationError
from app.config import Settings
from app.providers.flights.duffel_mapper import build_duffel_offer_request
from app.providers.flights.duffel_schemas import DuffelOfferRequestResponse
from app.providers.flights.schemas import FlightSearchInput


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
