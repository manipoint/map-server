"""SerpApi Google Flights adapter."""

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.common.exceptions import ProviderUnavailableError
from app.common.time import UtcClock, utc_now
from app.config import Settings
from app.domain.flights import FlightSearchStatus
from app.providers.flights.metadata_provider import FlightMetadataProvider
from app.providers.flights.schemas import (
    FlightItinerary,
    FlightOffer,
    FlightSearchInput,
    FlightSearchResult,
    FlightSegment,
)
from app.providers.serpapi.client import SerpApiClient

_FLIGHT_NUMBER = re.compile(r"^([A-Z0-9]{2,3})\s*([A-Z0-9]{1,8})$")
_CABIN_CODES = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}


class SerpApiFlightClient:
    """Search all returned Google Flights options and normalize each priced fare."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        metadata_provider: FlightMetadataProvider,
        settings: Settings,
        clock: UtcClock = utc_now,
    ) -> None:
        self.client = SerpApiClient(http_client=http_client, settings=settings)
        self.metadata_provider = metadata_provider
        self.max_results = settings.max_search_results
        self.clock = clock

    async def search_flights(self, *, request: FlightSearchInput) -> FlightSearchResult:
        params: dict[str, object] = {
            "departure_id": request.origin,
            "arrival_id": request.destination,
            "outbound_date": request.departure_date.isoformat(),
            "type": 1 if request.return_date else 2,
            "travel_class": _CABIN_CODES[request.cabin_class.value],
            "adults": request.adults,
            "children": len(request.children_ages),
            "children_ages": ",".join(map(str, request.children_ages)) or None,
            "infants_in_seat": len(request.infants_with_seat_ages),
            "infants_on_lap": len(request.infants_on_lap_ages),
            "currency": request.currency,
            "stops": 1 if request.nonstop_only else 0,
            "sort_by": 1,
        }
        if request.return_date is not None:
            params["return_date"] = request.return_date.isoformat()

        payload = await self.client.search(engine="google_flights", params=params)
        candidates = [
            candidate
            for result_key in ("best_flights", "other_flights")
            for candidate in payload.get(result_key, [])
            if isinstance(candidate, dict)
        ]
        candidates = candidates[: min(request.max_results, self.max_results)]
        metadata = await self._resolve_metadata(candidates)

        offers: list[FlightOffer] = []
        invalid_options = 0
        for candidate in candidates:
            try:
                offer = self._map_offer(
                    candidate=candidate,
                    request=request,
                    metadata=metadata,
                    booking_url=payload.get("google_flights_url"),
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                invalid_options += 1
                continue
            if offer is not None:
                offers.append(offer)
        offers.sort(
            key=lambda offer: (
                offer.total_price is None,
                offer.total_price if offer.total_price is not None else Decimal(0),
            )
        )

        return FlightSearchResult(
            status=(
                FlightSearchStatus.OFFERS_AVAILABLE
                if offers
                else FlightSearchStatus.NO_OFFERS
            ),
            searched_at=self.clock(),
            offers=offers,
            message=(
                None
                if offers
                else (
                    "Flight options were incomplete. Try the search again."
                    if invalid_options
                    else "No priced flight options were returned."
                )
            ),
        )

    async def _resolve_metadata(self, candidates: list[dict[str, Any]]):
        airport_codes: set[str] = set()
        carrier_codes: set[str] = set()
        for candidate in candidates:
            for flight in candidate.get("flights", []):
                departure = flight.get("departure_airport", {})
                arrival = flight.get("arrival_airport", {})
                airport_codes.update(
                    code
                    for code in (departure.get("id"), arrival.get("id"))
                    if isinstance(code, str)
                )
                match = _FLIGHT_NUMBER.fullmatch(str(flight.get("flight_number", "")))
                if match:
                    carrier_codes.add(match.group(1))
        metadata = await self.metadata_provider.resolve(
            airport_codes=frozenset(airport_codes),
            carrier_codes=frozenset(carrier_codes),
        )
        missing = airport_codes.difference(metadata.airports)
        if missing:
            raise ProviderUnavailableError(
                "Timezone metadata is missing for one or more flight airports"
            )
        return metadata

    @staticmethod
    def _map_offer(
        *,
        candidate: dict[str, Any],
        request: FlightSearchInput,
        metadata,
        booking_url: str | None,
    ) -> FlightOffer | None:
        raw_flights = candidate.get("flights")
        if not isinstance(raw_flights, list) or not raw_flights:
            raise ValueError("Flight option has no segments")
        parsed = [
            SerpApiFlightClient._map_segment(flight, metadata) for flight in raw_flights
        ]
        split_index = next(
            (
                index + 1
                for index, segment in enumerate(parsed[:-1])
                if segment.arrival_airport == request.destination
                and parsed[index + 1].departure_airport == request.destination
            ),
            len(parsed),
        )
        outbound = parsed[:split_index]
        returning = parsed[split_index:]
        if (
            not outbound
            or outbound[0].departure_airport != request.origin
            or outbound[-1].arrival_airport != request.destination
        ):
            raise ValueError("Flight option does not match the requested outbound")
        if request.return_date is not None and (
            not returning
            or returning[0].departure_airport != request.destination
            or returning[-1].arrival_airport != request.origin
        ):
            raise ValueError("Flight option is missing the requested return journey")
        price = candidate.get("price")
        amount = Decimal(str(price)) if price is not None else None
        if amount is not None and amount < 0:
            raise ValueError("Flight fare cannot be negative")
        identity = str(
            candidate.get("booking_token") or json.dumps(raw_flights, sort_keys=True)
        )
        offer_id = hashlib.sha256(identity.encode()).hexdigest()
        return FlightOffer(
            offer_id=offer_id,
            outbound=SerpApiFlightClient._itinerary(outbound),
            return_itinerary=(
                SerpApiFlightClient._itinerary(returning)
                if request.return_date is not None
                else None
            ),
            total_price=amount,
            currency=request.currency,
            traveler_count=request.total_travelers,
            booking_url=booking_url,
        )

    @staticmethod
    def _map_segment(flight: dict[str, Any], metadata) -> FlightSegment:
        departure = flight["departure_airport"]
        arrival = flight["arrival_airport"]
        departure_code = str(departure["id"]).upper()
        arrival_code = str(arrival["id"]).upper()
        departure_zone = metadata.airports[departure_code].time_zone
        arrival_zone = metadata.airports[arrival_code].time_zone
        match = _FLIGHT_NUMBER.fullmatch(str(flight["flight_number"]).upper())
        if match is None:
            raise ValueError("Flight number is invalid")
        departure_time = datetime.fromisoformat(departure["time"]).replace(
            tzinfo=ZoneInfo(departure_zone)
        )
        arrival_time = datetime.fromisoformat(arrival["time"]).replace(
            tzinfo=ZoneInfo(arrival_zone)
        )
        return FlightSegment(
            departure_airport=departure_code,
            arrival_airport=arrival_code,
            departure_at=departure_time,
            arrival_at=arrival_time,
            departure_time_zone=departure_zone,
            arrival_time_zone=arrival_zone,
            marketing_carrier_code=match.group(1),
            marketing_carrier_name=str(flight.get("airline") or match.group(1)),
            marketing_flight_number=match.group(2),
            duration_minutes=int(flight["duration"]),
        )

    @staticmethod
    def _itinerary(segments: list[FlightSegment]) -> FlightItinerary:
        minutes = int(
            (
                segments[-1].arrival_at.astimezone(ZoneInfo("UTC"))
                - segments[0].departure_at.astimezone(ZoneInfo("UTC"))
            ).total_seconds()
            / 60
        )
        return FlightItinerary(segments=segments, duration_minutes=max(minutes, 1))
