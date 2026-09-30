"""Bounded parallel research. Models never select or execute these tools."""

import asyncio
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl

from app.common.exceptions import ProviderUnavailableError
from app.common.time import utc_now
from app.domain.assistant_content import (
    AssistantHotelCard,
    AssistantMedia,
    AssistantMoney,
)
from app.domain.flights import FlightCabinClass
from app.domain.trip_requirements import TripRequirements, TripTransport
from app.domain.trips import TravelerParty, TripRequest
from app.mcp.client import TravelMcpClient
from app.mcp.schemas.flights import FlightSearchPreparationInput
from app.providers.flights.schemas import FlightSearchResult
from app.providers.hotels.schemas import HotelSearchResult
from app.providers.places.schemas import PlaceSearchResult
from app.services.trip_request_mapper import TripRequestMapper

RESEARCH_TIMEOUT_SECONDS = 15


class ResearchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(max_length=64)
    kind: Literal["place", "hotel", "flight"]
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(max_length=200)
    description: str = Field(max_length=1000)
    source_id: str | None = Field(default=None, max_length=300)
    source_urls: tuple[HttpUrl, ...] = Field(default=(), max_length=5)
    expires_at: AwareDatetime | None = None
    hotel_card: AssistantHotelCard | None = None


class PlanningResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    searched_at: AwareDatetime
    evidence: tuple[ResearchEvidence, ...] = Field(default=(), max_length=15)
    warnings: tuple[str, ...] = Field(default=(), max_length=10)


def to_trip_request(requirements: TripRequirements) -> TripRequest:
    """Map only complete requirements; provider defaults never fill user state."""
    infant_seating = iter(requirements.infant_on_lap or ())
    seated: list[int] = []
    lap: list[int] = []
    for age in requirements.minor_ages or ():
        if age < 2:
            (lap if next(infant_seating, False) else seated).append(age)
    return TripRequest(
        origin=requirements.origin,
        destination=requirements.destination,
        start_date=requirements.start_date,
        end_date=requirements.resolved_end_date,
        travelers=TravelerParty(
            adults=requirements.adults,
            children_ages=[age for age in requirements.minor_ages or () if age >= 2],
            infants_with_seat_ages=seated,
            infants_on_lap_ages=lap,
        ),
        rooms=requirements.rooms if requirements.needs_lodging else 1,
        interests=list(requirements.interests),
        total_budget=requirements.total_budget,
        # Search currency is a display default, not an inferred budget decision.
        budget_currency=requirements.budget_currency or "USD",
        cabin_class=requirements.cabin_class or FlightCabinClass.ECONOMY,
    )


class PlanningResearchService:
    def __init__(
        self,
        *,
        client: TravelMcpClient,
        places_available: bool,
        hotels_available: bool,
        round_trip_flights_available: bool,
    ) -> None:
        self.client = client
        self.places_available = places_available
        self.hotels_available = hotels_available
        self.round_trip_flights_available = round_trip_flights_available

    async def research(self, requirements: TripRequirements) -> PlanningResearch:
        request = to_trip_request(requirements)
        kinds = ["places"]
        if requirements.needs_lodging:
            kinds.append("hotels")
        if requirements.transport == TripTransport.FLIGHT:
            kinds.append("flights")
        results = await asyncio.gather(*(self._search(kind, request) for kind in kinds))
        return PlanningResearch(
            searched_at=utc_now(),
            evidence=tuple(item for evidence, _ in results for item in evidence),
            warnings=tuple(warning for _, warning in results if warning),
        )

    async def _search(
        self, kind: str, request: TripRequest
    ) -> tuple[list[ResearchEvidence], str | None]:
        enabled = {
            "places": self.places_available,
            "hotels": self.hotels_available,
            "flights": self.round_trip_flights_available,
        }[kind]
        if not enabled:
            return (
                [],
                f"{kind}: live search is unavailable; availability and prices are unverified.",
            )
        try:
            async with asyncio.timeout(RESEARCH_TIMEOUT_SECONDS):
                if kind == "places":
                    result = await self.client.search_places(
                        request=TripRequestMapper.to_place_search(
                            request, max_results=5
                        )
                    )
                elif kind == "hotels":
                    result = await self.client.search_hotels(
                        request=TripRequestMapper.to_hotel_search(
                            request, max_results=3
                        )
                    )
                else:
                    party = request.travelers
                    result = await self.client.search_flights(
                        request=FlightSearchPreparationInput(
                            origin=request.origin,
                            destination=request.destination,
                            departure_date=request.start_date,
                            return_date=request.end_date,
                            adults=party.adults,
                            children_ages=party.children_ages,
                            infants_with_seat_ages=party.infants_with_seat_ages,
                            infants_on_lap_ages=party.infants_on_lap_ages,
                            cabin_class=request.cabin_class,
                            currency=request.budget_currency,
                            max_results=3,
                        )
                    )
        except (ProviderUnavailableError, TimeoutError):
            return [], f"{kind}: search failed; availability and prices are unverified."
        evidence = _compact_evidence(result, now=utc_now())
        if not evidence:
            return (
                [],
                f"{kind}: no verified options; further details or a later search may be needed.",
            )
        return evidence, None


def _compact_evidence(result: object, *, now: datetime) -> list[ResearchEvidence]:
    evidence: list[ResearchEvidence] = []
    if isinstance(result, PlaceSearchResult):
        seen: set[str] = set()
        for place in result.places[:5]:
            key = place.provider_place_id or place.name.casefold()
            if key in seen:
                continue
            seen.add(key)
            evidence.append(
                ResearchEvidence(
                    id=f"place-{len(evidence) + 1}",
                    kind="place",
                    name=place.name,
                    location=(place.address or place.name)[:200],
                    description=place.summary,
                    source_id=place.provider_place_id,
                    source_urls=tuple(place.source_urls),
                )
            )
    elif isinstance(result, HotelSearchResult):
        for option in result.options[:3]:
            if option.expires_at <= now:
                continue
            hotel = option.hotel
            evidence.append(
                ResearchEvidence(
                    id=f"hotel-{len(evidence) + 1}",
                    kind="hotel",
                    name=hotel.name,
                    location=hotel.city_name,
                    description=hotel.description or hotel.name,
                    source_id=option.search_result_id,
                    expires_at=option.expires_at,
                    hotel_card=AssistantHotelCard(
                        id=option.search_result_id,
                        name=hotel.name,
                        location=hotel.city_name,
                        rating=hotel.rating,
                        review_score=hotel.review_score,
                        price=AssistantMoney(
                            amount=option.cheapest_total_price, currency=option.currency
                        ),
                        image=AssistantMedia(
                            url=hotel.photo_urls[0], alt_text=hotel.name
                        )
                        if hotel.photo_urls
                        else None,
                        expires_at=option.expires_at,
                    ),
                )
            )
    elif isinstance(result, FlightSearchResult):
        for offer in result.offers[:3]:
            if offer.expires_at is not None and offer.expires_at <= now:
                continue
            first, last = offer.outbound.segments[0], offer.outbound.segments[-1]
            evidence.append(
                ResearchEvidence(
                    id=f"flight-{len(evidence) + 1}",
                    kind="flight",
                    name=f"{first.departure_airport} to {last.arrival_airport}",
                    location=last.arrival_airport,
                    description=f"{first.departure_at.isoformat()} to {last.arrival_at.isoformat()}; quote {offer.total_price} {offer.currency}, not booked.",
                    expires_at=offer.expires_at,
                    source_id=offer.offer_id,
                )
            )
    return evidence
