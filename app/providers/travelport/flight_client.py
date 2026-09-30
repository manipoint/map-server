"""Travelport implementation of one-way and round-trip flight search."""

import asyncio
from uuid import uuid4

import httpx

from app.common.exceptions import (
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.common.time import UtcClock, utc_now
from app.config import Settings
from app.domain.flights import FlightSearchStatus
from app.providers.flights.metadata_provider import FlightMetadataProvider
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult
from app.providers.travelport.auth_client import TravelportAuthClient
from app.providers.travelport.flight_metadata_resolver import (
    resolve_travelport_metadata,
)
from app.providers.travelport.flight_request_mapper import (
    TRAVELPORT_MAX_SEARCH_TRAVELERS,
    build_travelport_search_request,
)
from app.providers.travelport.flight_response_decoder import (
    DecodedTravelportSearch,
    decode_travelport_search,
)
from app.providers.travelport.flight_response_mapper import (
    map_travelport_one_way_result,
)
from app.providers.travelport.response_reader import read_travelport_json
from app.providers.travelport.round_trip_mapper import map_travelport_round_trip_result


class TravelportFlightClient:
    """Search flights using shared HTTP, authentication and metadata clients."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        auth_client: TravelportAuthClient,
        metadata_provider: FlightMetadataProvider,
        settings: Settings,
        clock: UtcClock = utc_now,
    ) -> None:
        pcc = settings.travelport_pcc_core

        if pcc is None or not pcc.strip():
            raise ProviderConfigurationError("Travelport PCC is required")

        self._http_client = http_client
        self._auth_client = auth_client
        self._metadata_provider = metadata_provider
        self._clock = clock
        self._pcc = pcc.strip()
        self._timeout = settings.provider_timeout_seconds
        self._max_response_bytes = settings.travelport_max_response_bytes
        self._search_url = (
            settings.travelport_air_base_url.rstrip("/")
            + "/catalog/search/catalogproductofferings"
        )

    async def search_flights(
        self,
        *,
        request: FlightSearchInput,
    ) -> FlightSearchResult:
        """Execute a journey search within one bounded provider deadline."""

        searched_at = self._clock()

        if request.total_travelers > TRAVELPORT_MAX_SEARCH_TRAVELERS:
            return FlightSearchResult(
                status=FlightSearchStatus.GROUP_BOOKING_REQUIRED,
                searched_at=searched_at,
                message=("This search requires group-booking assistance."),
            )

        payload = build_travelport_search_request(request).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        search_id = uuid4()

        try:
            # One deadline includes authentication, search and metadata lookup.
            async with asyncio.timeout(self._timeout):
                decoded = await self._search(
                    payload=payload,
                    trace_id=str(search_id),
                )

                metadata = await resolve_travelport_metadata(
                    decoded=decoded,
                    provider=self._metadata_provider,
                )

                mapper = (
                    map_travelport_round_trip_result
                    if request.return_date is not None
                    else map_travelport_one_way_result
                )
                return mapper(
                    request=request,
                    decoded=decoded,
                    metadata=metadata,
                    search_id=search_id,
                    searched_at=searched_at,
                )

        except (httpx.HTTPError, TimeoutError):
            raise ProviderUnavailableError(
                "Travelport flight search is unavailable"
            ) from None

    async def _search(
        self,
        *,
        payload: dict[str, object],
        trace_id: str,
    ) -> DecodedTravelportSearch:
        """Allow one authentication recovery, without general search retries."""

        for attempt in range(2):
            token = await self._auth_client.get_access_token()

            async with self._http_client.stream(
                "POST",
                self._search_url,
                json=payload,
                headers={
                    "Authorization": (f"Bearer {token.get_secret_value()}"),
                    "TVP-PCC-Core": self._pcc,
                    "TraceId": trace_id,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code == 401:
                    self._auth_client.invalidate(
                        rejected_token=token,
                    )

                    if attempt == 0:
                        continue

                    raise ProviderConfigurationError(
                        "Travelport rejected flight search authentication"
                    )

                if response.status_code == 403:
                    raise ProviderConfigurationError(
                        "Travelport flight search access was denied"
                    )

                response.raise_for_status()

                response_payload = await read_travelport_json(
                    response=response,
                    max_bytes=self._max_response_bytes,
                )

                return decode_travelport_search(response_payload)

        raise ProviderUnavailableError("Travelport flight search could not complete")
