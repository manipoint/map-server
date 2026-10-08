"""Typed standalone travel requests with deterministic, provider-grounded replies."""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.common.exceptions import ProviderUnavailableError
from app.common.time import utc_now
from app.domain.clarifications import TravelClarification, build_airport_input_request
from app.domain.flights import ONE_WAY_ONLY_MESSAGE
from app.mcp.client import TravelMcpClient
from app.mcp.schemas.currency import CurrencyConversionGuidance
from app.mcp.schemas.flights import (
    FlightSearchGuidance,
    FlightSearchPreparationGuidance,
    FlightSearchPreparationInput,
)
from app.mcp.schemas.hotels import HotelSearchGuidance
from app.mcp.schemas.places import PlaceSearchGuidance
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)
from app.providers.flights.schemas import FlightSearchResult
from app.providers.hotels.schemas import HotelSearchInput, HotelSearchResult
from app.providers.places.schemas import PlaceSearchInput, PlaceSearchResult
from app.providers.weather.schemas import CurrentWeather


class WeatherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["weather"]
    arguments: CurrentWeatherInput


class CurrencyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["currency"]
    arguments: CurrencyConversionInput


class PlacesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["places"]
    arguments: PlaceSearchInput


class FlightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["flights"]
    arguments: FlightSearchPreparationInput


class HotelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["hotels"]
    arguments: HotelSearchInput
    budget_decision: Literal["specified", "no_limit"] | None = None

    @model_validator(mode="after")
    def validate_budget_decision(self) -> "HotelsRequest":
        if (
            self.budget_decision == "specified"
            and self.arguments.max_total_price is None
        ):
            raise ValueError("specified hotel budget requires max_total_price")
        if (
            self.budget_decision == "no_limit"
            and self.arguments.max_total_price is not None
        ):
            raise ValueError("no-limit hotel search cannot have max_total_price")
        if self.budget_decision is None and self.arguments.max_total_price is not None:
            raise ValueError("hotel budget amount requires an explicit decision")
        return self


# Literal tags remain unambiguous; a plain union emits provider-supported anyOf.
StandaloneRequest = (
    WeatherRequest | CurrencyRequest | PlacesRequest | FlightsRequest | HotelsRequest
)


class SearchReply(BaseModel):
    content: str
    clarification: TravelClarification | None = None
    input_required: bool = False


def guidance_reply(result: object) -> SearchReply | None:
    """Preserve provider guidance instead of reducing it to empty evidence."""
    if isinstance(result, FlightSearchPreparationGuidance):
        requests = [
            request
            for field, resolution in (
                ("origin", result.origin),
                ("destination", result.destination),
            )
            if (
                request := build_airport_input_request(
                    field=field, resolution=resolution
                )
            )
            is not None
        ]
        return SearchReply(
            content="\n".join(request.question for request in requests),
            clarification=TravelClarification(requests=requests),
            input_required=True,
        )
    if isinstance(result, (PlaceSearchGuidance, HotelSearchGuidance)):
        return SearchReply(
            content="\n".join((result.message, *result.candidates)), input_required=True
        )
    if isinstance(result, (FlightSearchGuidance, CurrencyConversionGuidance)):
        if result.message == ONE_WAY_ONLY_MESSAGE:
            return SearchReply(
                content="Only one-way flight searches are available. Would you like an outbound-only search?",
                input_required=True,
            )
        return SearchReply(content=result.message, input_required=True)
    if (
        isinstance(result, FlightSearchResult)
        and result.status == "group_booking_required"
    ):
        return SearchReply(content=result.message, input_required=True)
    return None


class StandaloneSearchService:
    def __init__(
        self,
        *,
        client: TravelMcpClient,
        enabled: frozenset[str],
        timeout_seconds: float = 15,
    ) -> None:
        self.client = client
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds

    async def search(self, request: StandaloneRequest) -> SearchReply:
        if request.kind not in self.enabled:
            return SearchReply(
                content=f"Live {request.kind} search is unavailable. No availability or price has been verified."
            )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                if isinstance(request, WeatherRequest):
                    result = await self.client.get_current_weather(
                        city=request.arguments.city
                    )
                elif isinstance(request, CurrencyRequest):
                    result = await self.client.convert_currency(
                        request=request.arguments
                    )
                elif isinstance(request, PlacesRequest):
                    result = await self.client.search_places(request=request.arguments)
                elif isinstance(request, FlightsRequest):
                    result = await self.client.search_flights(request=request.arguments)
                else:
                    result = await self.client.search_hotels(request=request.arguments)
        except (ProviderUnavailableError, TimeoutError):
            return SearchReply(
                content="Verified travel data is temporarily unavailable. Please retry later."
            )
        guidance = guidance_reply(result)
        if guidance is not None:
            return guidance
        if isinstance(result, CurrentWeather):
            text = f"{result.location}: {result.condition}, {result.temperature_c:g} °C. Observed {result.observed_at.isoformat()}. Current conditions only, not a forecast."
        elif isinstance(result, CurrencyConversionResult):
            text = f"{result.amount} {result.base_currency} = {result.converted_amount} {result.quote_currency}. Reference rate: {result.rate} ({result.rate_date}); not a payment quote."
        elif isinstance(result, PlaceSearchResult):
            text = (
                "\n".join(f"• {p.name}: {p.summary}" for p in result.places[:5])
                or "No matching places were found."
            )
        elif isinstance(result, FlightSearchResult):
            lines = []
            for offer in result.offers:
                if offer.expires_at is not None and offer.expires_at <= utc_now():
                    continue
                legs = [offer.outbound] + (
                    [offer.return_itinerary] if offer.return_itinerary else []
                )
                schedule = "; ".join(
                    f"{leg.segments[0].departure_airport} → {leg.segments[-1].arrival_airport}: {leg.segments[0].departure_at.isoformat()} to {leg.segments[-1].arrival_at.isoformat()}"
                    for leg in legs
                )
                price_text = (
                    f"{offer.total_price} {offer.currency}"
                    if offer.total_price is not None
                    else "price unavailable"
                )
                lines.append(
                    f"• {schedule}; {price_text} for {offer.traveler_count} travellers."
                )
            text = "\n".join(lines) or "No current flight offers were found."
            text += "\nPrices and availability may change. Nothing is booked."
        elif isinstance(result, HotelSearchResult):
            text = (
                "\n".join(
                    f"• {o.hotel.name} ({o.provider_source or 'provider'}): "
                    + (
                        f"{o.cheapest_total_price} {o.currency}."
                        if o.cheapest_total_price is not None
                        else "price unavailable."
                    )
                    for o in result.options
                    if o.expires_at > utc_now()
                )
                or result.message
                or "No current hotel options were found."
            )
            text += "\nPrices and availability may change. Nothing is booked."
        else:
            raise ProviderUnavailableError("Search returned an invalid result")
        return SearchReply(content=text)
