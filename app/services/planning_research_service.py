"""Bounded parallel research. Models never select or execute these tools."""

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.exceptions import ProviderUnavailableError
from app.common.time import utc_now
from app.database.repositories.destinations import DestinationRepository
from app.domain.assistant_content import (
    AssistantHotelCard,
    AssistantMedia,
    AssistantMoney,
)
from app.domain.destinations import MediaAssetValue
from app.domain.flights import FlightCabinClass
from app.domain.trip_requirements import TripRequirements, TripTransport
from app.domain.trips import TravelerParty, TripRequest
from app.mcp.client import TravelMcpClient
from app.mcp.schemas.flights import FlightSearchPreparationInput
from app.providers.flights.schemas import FlightSearchResult
from app.providers.hotels.schemas import HotelSearchResult
from app.providers.places.schemas import PlaceSearchResult
from app.services.standalone_search_service import SearchReply, guidance_reply
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
    image: AssistantMedia | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    start_time_zone: str | None = None
    end_time_zone: str | None = None


class PlanningResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    searched_at: AwareDatetime
    evidence: tuple[ResearchEvidence, ...] = Field(default=(), max_length=15)
    warnings: tuple[str, ...] = Field(default=(), max_length=10)
    cover_image: AssistantMedia | None = None
    guidance: tuple[SearchReply, ...] = Field(default=(), max_length=3)
    time_zone: str | None = None


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
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.client = client
        self.places_available = places_available
        self.hotels_available = hotels_available
        self.round_trip_flights_available = round_trip_flights_available
        self.session_factory = session_factory

    def cache_key(self, requirements: TripRequirements) -> str:
        payload = {
            "request": to_trip_request(requirements).model_dump(mode="json"),
            "transport": requirements.transport,
            "lodging": requirements.needs_lodging,
            "providers": [
                self.places_available,
                self.hotels_available,
                self.round_trip_flights_available,
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def research(self, requirements: TripRequirements) -> PlanningResearch:
        request = to_trip_request(requirements)
        catalogue = await self._catalogue_research(requirements.destination)
        kinds = [] if catalogue.evidence else ["places"]
        if requirements.needs_lodging:
            kinds.append("hotels")
        if requirements.transport == TripTransport.FLIGHT:
            kinds.append("flights")
        results = await asyncio.gather(*(self._search(kind, request) for kind in kinds))
        return PlanningResearch(
            searched_at=utc_now(),
            evidence=catalogue.evidence
            + tuple(item for evidence, _, _ in results for item in evidence),
            warnings=tuple(warning for _, warning, _ in results if warning),
            cover_image=catalogue.cover_image,
            guidance=tuple(reply for _, _, reply in results if reply is not None),
            time_zone=next(
                (
                    item.end_time_zone
                    for entries, _, _ in results
                    for item in entries
                    if item.kind == "flight" and not item.id.endswith("-return")
                ),
                None,
            ),
        )

    async def _catalogue_research(self, destination: str) -> PlanningResearch:
        """Reuse published catalogue identities and active media in bounded reads."""
        empty = PlanningResearch(searched_at=utc_now())
        if self.session_factory is None:
            return empty
        async with self.session_factory() as session:
            repository = DestinationRepository(session)
            candidate = await repository.find_for_planning(query=destination)
            if candidate is None:
                return empty
            places = await repository.list_published_places(
                destination_id=candidate.id, limit=5
            )
        return PlanningResearch(
            searched_at=utc_now(),
            cover_image=_catalogue_image(candidate.cover_image),
            evidence=tuple(
                ResearchEvidence(
                    id=f"place-{index}",
                    kind="place",
                    name=place.name,
                    location=(place.address or candidate.name)[:200],
                    description=place.summary,
                    source_id=str(place.id),
                    image=_catalogue_image(place.cover_image),
                )
                for index, place in enumerate(places, start=1)
            ),
        )

    async def _search(
        self, kind: str, request: TripRequest
    ) -> tuple[list[ResearchEvidence], str | None, SearchReply | None]:
        enabled = {
            "places": self.places_available,
            "hotels": self.hotels_available,
            "flights": self.round_trip_flights_available,
        }[kind]
        if not enabled:
            return (
                [],
                f"{kind}: live search is unavailable; availability and prices are unverified.",
                None,
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
            return (
                [],
                f"{kind}: search failed; availability and prices are unverified.",
                None,
            )
        guidance = guidance_reply(result)
        if guidance is not None:
            return [], None, guidance
        evidence = _compact_evidence(result, now=utc_now())
        if not evidence:
            return (
                [],
                f"{kind}: no verified options; further details or a later search may be needed.",
                None,
            )
        return evidence, None, None


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
        for index, offer in enumerate(result.offers[:3], start=1):
            if offer.expires_at is not None and offer.expires_at <= now:
                continue
            for suffix, leg in (
                ("", offer.outbound),
                ("-return", offer.return_itinerary),
            ):
                if leg is None:
                    continue
                first, last = leg.segments[0], leg.segments[-1]
                # Legacy providers may return naive timestamps: never invent offsets.
                if (
                    first.departure_at.utcoffset() is None
                    or last.arrival_at.utcoffset() is None
                ):
                    continue
                evidence.append(
                    ResearchEvidence(
                        id=f"flight-{index}{suffix}",
                        kind="flight",
                        name=f"{first.departure_airport} to {last.arrival_airport}",
                        location=last.arrival_airport,
                        description=f"{first.departure_at.isoformat()} to {last.arrival_at.isoformat()}; quote {offer.total_price} {offer.currency}, not booked.",
                        expires_at=offer.expires_at,
                        source_id=offer.offer_id,
                        starts_at=first.departure_at,
                        ends_at=last.arrival_at,
                        start_time_zone=first.departure_time_zone,
                        end_time_zone=last.arrival_time_zone,
                    )
                )
    return evidence


def _catalogue_image(media: MediaAssetValue | None) -> AssistantMedia | None:
    if media is None:
        return None
    try:
        return AssistantMedia(
            url=media.url,
            alt_text=media.alt_text,
            width=media.width,
            height=media.height,
        )
    except ValidationError:
        # Invalid legacy media must not prevent a valid itinerary from generating.
        return None
