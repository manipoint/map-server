"""Resolve a route and discover bounded flight deals for flexible dates."""

import asyncio
import hashlib
from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.common.time import utc_now
from app.domain.trip_rules import validate_distinct_locations
from app.domain.value_objects import CurrencyCode
from app.providers.airports.schemas import AirportResolution, AirportSearchInput
from app.providers.serpapi.deals_client import FlightDeal, SerpApiDealsClient
from app.services.airport_resolution_service import AirportResolutionService


class DealDiscoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    origin: str = Field(min_length=2, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    window_start: date
    window_end: date
    adults: int = Field(ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants_in_seat: int = Field(default=0, ge=0, le=9)
    infants_on_lap: int = Field(default=0, ge=0, le=9)
    currency: CurrencyCode = "USD"

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        validate_distinct_locations(self.origin, self.destination)
        days = (self.window_end - self.window_start).days + 1
        if not 1 <= days <= 31:
            raise ValueError("Deal search window must contain 1–31 days")
        travelers = (
            self.adults + self.children + self.infants_in_seat + self.infants_on_lap
        )
        if travelers > 9:
            raise ValueError("Deal search supports up to nine travelers")
        if self.infants_on_lap > self.adults:
            raise ValueError("Each lap infant requires an adult")
        return self


class DealChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str = Field(min_length=1, max_length=64)
    deal: FlightDeal


class DealDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["airport_resolution_required", "no_deals", "deals_available"]
    origin: AirportResolution
    destination: AirportResolution
    options: tuple[DealChoice, ...] = Field(default=(), max_length=10)


class DealDiscoverySnapshot(BaseModel):
    """Bounded conversation-scoped result; prevents repeated paid searches."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request_key: str = Field(min_length=64, max_length=64)
    searched_at: AwareDatetime
    request: DealDiscoveryInput | None = None
    result: DealDiscoveryResult
    reply: str | None = Field(default=None, max_length=8_000)


class DealDiscoveryService:
    def __init__(
        self,
        *,
        airport_resolution_service: AirportResolutionService,
        deals_client: SerpApiDealsClient,
    ) -> None:
        self.airports = airport_resolution_service
        self.deals = deals_client

    async def discover(self, *, request: DealDiscoveryInput) -> DealDiscoveryResult:
        origin, destination = await asyncio.gather(
            self.airports.resolve_airport(
                request=AirportSearchInput(query=request.origin)
            ),
            self.airports.resolve_airport(
                request=AirportSearchInput(query=request.destination),
            ),
        )
        if origin.status != "resolved" or destination.status != "resolved":
            return DealDiscoveryResult(
                status="airport_resolution_required",
                origin=origin,
                destination=destination,
            )
        assert origin.iata_code is not None
        assert destination.iata_code is not None

        if origin.iata_code == destination.iata_code:
            raise ValueError("Origin and destination resolve to the same airport")

        if request.window_start < utc_now().date():
            raise ValueError("Deal search window cannot start in the past")

        found = await self.deals.search_deals(
            origin_code=origin.iata_code,
            destination_codes={destination.iata_code},
            window_start=request.window_start,
            window_end=request.window_end,
            currency=request.currency,
            adults=request.adults,
            children=request.children,
            infants_in_seat=request.infants_in_seat,
            infants_in_lap=request.infants_on_lap,
        )
        options = tuple(
            DealChoice(option_id=self._option_id(deal), deal=deal)
            for deal in found[:10]
        )
        return DealDiscoveryResult(
            status="deals_available" if options else "no_deals",
            origin=origin,
            destination=destination,
            options=options,
        )

    @staticmethod
    def _option_id(deal: FlightDeal) -> str:
        identity = "|".join(
            (
                deal.origin,
                deal.destination,
                deal.start_date.isoformat(),
                deal.end_date.isoformat(),
                str(deal.price),
                deal.currency,
                deal.flight_link,
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()[:32]
