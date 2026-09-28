"""Map Travelport flight details into provider-independent models."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.common.exceptions import ProviderUnavailableError
from app.domain.flights import FlightSearchStatus
from app.providers.flights.metadata_schemas import FlightMetadata
from app.providers.flights.schemas import (
    FlightItinerary,
    FlightOffer,
    FlightSearchInput,
    FlightSearchResult,
    FlightSegment,
)
from app.providers.travelport.flight_request_mapper import (
    build_travelport_search_modifiers,
)
from app.providers.travelport.flight_response_decoder import DecodedTravelportSearch
from app.providers.travelport.flight_response_schemas import (
    TravelportFlightResponse,
    TravelportProductBrandOffering,
    TravelportProductResponse,
)
from app.providers.travelport.flight_schedule_mapper import (
    resolve_flight_endpoint_time,
)


def duration_in_minutes(duration: timedelta) -> int:
    """Convert a positive whole-minute duration without truncation."""

    minutes, remainder = divmod(
        duration,
        timedelta(minutes=1),
    )

    if minutes < 1 or remainder != timedelta(0):
        raise ProviderUnavailableError(
            "Flight duration must contain positive whole minutes"
        )

    return minutes


def map_travelport_flight(
    *,
    flight: TravelportFlightResponse,
    metadata: FlightMetadata,
) -> FlightSegment:
    """Preserve schedule, airline identity and intermediate stops."""
    try:
        departure_airport = metadata.airports[flight.departure.location]
        arrival_airport = metadata.airports[flight.arrival.location]
        marketing_airline = metadata.airlines[flight.carrier]

        operating_name = flight.operating_carrier_name
        if flight.operating_carrier is not None:
            operating_airline = metadata.airlines[flight.operating_carrier]
            if operating_name is None:
                operating_name = operating_airline.name

        departure_at = resolve_flight_endpoint_time(
            endpoint=flight.departure, airport=departure_airport
        )
        arrival_at = resolve_flight_endpoint_time(
            endpoint=flight.arrival,
            airport=arrival_airport,
        )
        return FlightSegment(
            departure_airport=flight.departure.location,
            arrival_airport=flight.arrival.location,
            departure_at=departure_at,
            arrival_at=arrival_at,
            departure_time_zone=departure_airport.time_zone,
            arrival_time_zone=arrival_airport.time_zone,
            marketing_carrier_code=flight.carrier,
            marketing_carrier_name=marketing_airline.name,
            marketing_flight_number=flight.number,
            operating_carrier_code=flight.operating_carrier,
            operating_carrier_name=operating_name,
            operating_flight_number=flight.operating_carrier_number,
            duration_minutes=duration_in_minutes(flight.duration),
            intermediate_stops=flight.stops,
        )

    except KeyError:
        raise ProviderUnavailableError(
            "Required flight metadata is unavailable"
        ) from None

    except ValidationError:
        raise ProviderUnavailableError(
            "Travelport returned invalid flight segment data"
        ) from None


def map_travelport_itinerary(
    *,
    product: TravelportProductResponse,
    flights_by_id: Mapping[str, TravelportFlightResponse],
    metadata: FlightMetadata,
) -> FlightItinerary:
    """Build an ordered, connected itinerary from product references."""
    ordered_segments = sorted(
        product.segments,
        key=lambda segment: segment.sequence,
    )
    segments: list[FlightSegment] = []
    for reference in ordered_segments:
        flight = flights_by_id.get(reference.flight.flight_ref)
        if flight is None:
            raise ProviderUnavailableError(
                "Travelport product references an unavailable flight"
            )

        segment = map_travelport_flight(flight=flight, metadata=metadata)
        if segments:
            previous = segments[-1]
            if previous.arrival_airport != segment.departure_airport:
                raise ProviderUnavailableError(
                    "Travelport itinerary requires an unsupported airport transfer"
                )
            if segment.departure_at <= previous.arrival_at:
                raise ProviderUnavailableError(
                    "Travelport itinerary contains overlapping flight times"
                )
        segments.append(segment)

    duration_minutes = duration_in_minutes(product.total_duration)
    elapsed_duration = segments[-1].arrival_at - segments[0].departure_at
    if product.total_duration != elapsed_duration:
        raise ProviderUnavailableError(
            "Travelport itinerary duration does not match its schedule"
        )
    minimum_duration = sum(seg.duration_minutes for seg in segments)

    if duration_minutes < minimum_duration:
        raise ProviderUnavailableError(
            "Travelport itinerary duration is shorter than its flights"
        )

    try:
        return FlightItinerary(segments=segments, duration_minutes=duration_minutes)
    except ValidationError:
        raise ProviderUnavailableError(
            "Travelport returned invalid itinerary data"
        ) from None


def map_travelport_one_way_offer(
    *,
    offer_id: str,
    request: FlightSearchInput,
    fare: TravelportProductBrandOffering,
    decoded: DecodedTravelportSearch,
    metadata: FlightMetadata,
) -> FlightOffer | None:
    """Map one fare, or exclude it when it does not match preferences."""

    if request.return_date is not None:
        raise ProviderUnavailableError(
            "One-way offer mapping cannot process a return search"
        )

    if len(fare.products) != 1:
        raise ProviderUnavailableError("Unsupported one-way product combination")

    product = decoded.products_by_id.get(fare.products[0].product_ref)
    if product is None:
        raise ProviderUnavailableError(
            "Travelport fare references an unavailable product"
        )

    traveler_count = sum(
        passenger.passenger_quantity for passenger in product.passenger_flights
    )
    if traveler_count != request.total_travelers:
        raise ProviderUnavailableError(
            "Travelport fare traveler count does not match the request"
        )

    itinerary = map_travelport_itinerary(
        product=product, flights_by_id=decoded.flights_by_id, metadata=metadata
    )
    first_segment = itinerary.segments[0]
    last_segment = itinerary.segments[-1]

    if (
        first_segment.departure_airport != request.origin
        or last_segment.arrival_airport != request.destination
    ):
        return None

    departure_date = first_segment.departure_at.astimezone(
        ZoneInfo(first_segment.departure_time_zone)
    ).date()

    if departure_date != request.departure_date:
        return None
    if request.nonstop_only and itinerary.stops > 0:
        return None
    requested_cabin = (
        build_travelport_search_modifiers(request=request)
        .cabin_preferences[0]
        .cabins[0]
    )

    for passenger in product.passenger_flights:
        for flight_product in passenger.flight_products:
            if flight_product.cabin != requested_cabin:
                return None

    price = fare.best_combinable_price
    try:
        return FlightOffer(
            offer_id=offer_id,
            outbound=itinerary,
            total_price=price.total_price,
            currency=price.currency.value,
            traveler_count=traveler_count,
        )
    except ValidationError:
        raise ProviderUnavailableError(
            "Travelport returned invalid flight offer data"
        ) from None


def map_travelport_one_way_result(
    *,
    request: FlightSearchInput,
    decoded: DecodedTravelportSearch,
    metadata: FlightMetadata,
    search_id: UUID,
    searched_at: datetime,
) -> FlightSearchResult:
    """Build a bounded result from matching one-way fares."""
    if request.return_date is not None:
        raise ProviderUnavailableError(
            "One-way result mapping cannot process a return search"
        )
    if searched_at.utcoffset() is None:
        raise ValueError("searched_at must include a timezone")

    offers: list[FlightOffer] = []
    result_currency: str | None = None

    for catalog_index, catalog in enumerate(decoded.catalog.offerings):
        for options_index, options in enumerate(catalog.product_brand_options):
            for fare_index, fare in enumerate(options.offerings):
                offer_id = str(
                    uuid5(
                        search_id,
                        f"{catalog_index}:{options_index}:{fare_index}",
                    )
                )
                offer = map_travelport_one_way_offer(
                    offer_id=offer_id,
                    request=request,
                    fare=fare,
                    decoded=decoded,
                    metadata=metadata,
                )
                if offer is None:
                    continue
                if result_currency is None:
                    result_currency = offer.currency
                elif offer.currency != result_currency:
                    raise ProviderUnavailableError(
                        "Travelport returned offers in inconsistent currencies"
                    )
                offers.append(offer)

    offers.sort(key=lambda offer: offer.total_price)
    selected_offers = offers[: request.max_results]

    status = (
        FlightSearchStatus.OFFERS_AVAILABLE
        if selected_offers
        else FlightSearchStatus.NO_OFFERS
    )
    return FlightSearchResult(
        status=status,
        searched_at=searched_at.astimezone(UTC),
        offers=selected_offers,
    )
