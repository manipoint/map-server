"""Map normalized flight searches to Duffel request payloads."""

from datetime import datetime, timedelta
from math import ceil

from app.domain.flights import FlightSearchStatus
from app.providers.flights.duffel_schemas import (
    DuffelOfferRequestData,
    DuffelOfferRequestPayload,
    DuffelOfferResponse,
    DuffelOffersListResponse,
    DuffelPassenger,
    DuffelSegmentResponse,
    DuffelSlice,
    DuffelSliceResponse,
)
from app.providers.flights.schemas import (
    FlightItinerary,
    FlightOffer,
    FlightSearchInput,
    FlightSearchResult,
    FlightSegment,
)


def build_duffel_offer_request(
    request: FlightSearchInput,
) -> DuffelOfferRequestPayload:
    """Convert one normalized flight search into a Duffel payload."""
    slices = [
        DuffelSlice(
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
        )
    ]
    if request.return_date is not None:
        slices.append(
            DuffelSlice(
                origin=request.destination,
                destination=request.origin,
                departure_date=request.return_date,
            )
        )
    passengers = [DuffelPassenger(type="adult") for _ in range(request.adults)]
    passengers.extend(DuffelPassenger(age=age) for age in request.children_ages)
    passengers.extend(
        DuffelPassenger(age=age) for age in request.infants_with_seat_ages
    )
    passengers.extend(
        DuffelPassenger(type="infant_without_seat") for _ in request.infants_on_lap_ages
    )
    return DuffelOfferRequestPayload(
        data=DuffelOfferRequestData(
            slices=slices,
            passengers=passengers,
            cabin_class=request.cabin_class,
            max_connections=0 if request.nonstop_only else None,
        )
    )


def duration_minutes(duration: timedelta) -> int:
    """Convert provider duration to whole minutes without understating it."""

    return ceil(duration.total_seconds() / 60)


def map_duffel_segment(
    segment: DuffelSegmentResponse,
) -> FlightSegment:
    """Map one Duffel segment to normalized flight output."""

    return FlightSegment(
        departure_airport=segment.origin.iata_code,
        arrival_airport=segment.destination.iata_code,
        departure_at=segment.departing_at,
        arrival_at=segment.arriving_at,
        departure_time_zone=segment.origin.time_zone,
        arrival_time_zone=segment.destination.time_zone,
        marketing_carrier_code=segment.marketing_carrier.iata_code,
        marketing_carrier_name=segment.marketing_carrier.name,
        marketing_flight_number=segment.marketing_carrier_flight_number,
        operating_carrier_code=segment.operating_carrier.iata_code,
        operating_carrier_name=segment.operating_carrier.name,
        operating_flight_number=segment.operating_carrier_flight_number,
        duration_minutes=duration_minutes(segment.duration),
    )


def map_duffel_itinerary(
    flight_slice: DuffelSliceResponse,
) -> FlightItinerary:
    """Map one Duffel slice to a normalized itinerary."""

    return FlightItinerary(
        segments=[map_duffel_segment(segment) for segment in flight_slice.segments],
        duration_minutes=duration_minutes(flight_slice.duration),
    )


def map_duffel_offer(
    offer: DuffelOfferResponse,
    *,
    expected_travelers: int,
    expected_slices: int,
) -> FlightOffer:
    """Map one complete Duffel offer."""

    if len(offer.slices) != expected_slices:
        raise ValueError("Duffel offer slice count does not match the search")

    traveler_count = len(offer.passengers)
    if traveler_count != expected_travelers:
        raise ValueError("Duffel offer passenger count does not match the search")

    outbound = map_duffel_itinerary(offer.slices[0])
    return_itinerary = (
        map_duffel_itinerary(offer.slices[1]) if expected_slices == 2 else None
    )

    return FlightOffer(
        offer_id=offer.id,
        outbound=outbound,
        return_itinerary=return_itinerary,
        total_price=offer.total_amount,
        currency=offer.total_currency,
        traveler_count=traveler_count,
        seats_available=None,
        refundable=None,
        expires_at=offer.expires_at,
    )


def map_duffel_search_result(
    *,
    response: DuffelOffersListResponse,
    request: FlightSearchInput,
    searched_at: datetime,
) -> FlightSearchResult:
    """Build one bounded normalized result from Duffel offers."""

    if searched_at.utcoffset() is None:
        raise ValueError("searched_at must include a timezone")

    current_offers = [
        offer for offer in response.data if offer.expires_at > searched_at
    ]

    selected_offers = current_offers[: request.max_results]
    expected_slices = 2 if request.return_date is not None else 1

    if not selected_offers:
        return FlightSearchResult(
            status=FlightSearchStatus.NO_OFFERS,
            searched_at=searched_at,
            message="No current flight offers were found for this search.",
        )

    return FlightSearchResult(
        status=FlightSearchStatus.OFFERS_AVAILABLE,
        searched_at=searched_at,
        offers=[
            map_duffel_offer(
                offer,
                expected_travelers=request.total_travelers,
                expected_slices=expected_slices,
            )
            for offer in selected_offers
        ],
    )
