"""Map normalized flight requests to Travelport request components."""

from collections import Counter
from typing import Literal

from app.domain.flights import FlightCabinClass
from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.flight_request_schemas import (
    TravelportCabin,
    TravelportCabinPreference,
    TravelportConnectionPreference,
    TravelportFlightSearchQuery,
    TravelportFlightSearchRequest,
    TravelportFlightType,
    TravelportLocationCode,
    TravelportPassengerCriteria,
    TravelportPricingModifiersAir,
    TravelportSearchCriteriaFlight,
    TravelportSearchModifiersAir,
)

TRAVELPORT_MAX_SEARCH_TRAVELERS = 9
_TRAVELPORT_CABINS: dict[FlightCabinClass, TravelportCabin] = {
    FlightCabinClass.ECONOMY: "Economy",
    FlightCabinClass.PREMIUM_ECONOMY: "PremiumEconomy",
    FlightCabinClass.BUSINESS: "Business",
    FlightCabinClass.FIRST: "First",
}


def build_travelport_passengers(
    request: FlightSearchInput,
) -> list[TravelportPassengerCriteria]:
    """Preserve passenger categories and ages in Travelport criteria."""

    if request.total_travelers > TRAVELPORT_MAX_SEARCH_TRAVELERS:
        raise ValueError("Travelport search supports at most 9 travelers")

    passengers = [
        TravelportPassengerCriteria(
            number=request.adults,
            passenger_type_code="ADT",
        ),
    ]

    age_groups: tuple[
        tuple[Literal["CNN", "INS", "INF"], list[int]],
        ...,
    ] = (
        ("CNN", request.children_ages),
        ("INS", request.infants_with_seat_ages),
        ("INF", request.infants_on_lap_ages),
    )

    for passenger_type, ages in age_groups:
        for age, count in sorted(Counter(ages).items()):
            passengers.append(
                TravelportPassengerCriteria(
                    number=count,
                    passenger_type_code=passenger_type,
                    age=age,
                )
            )

    return passengers


def build_travelport_routes(
    request: FlightSearchInput,
) -> list[TravelportSearchCriteriaFlight]:
    """Map one-way or round-trip searches without changing travel dates."""

    origin = TravelportLocationCode(value=request.origin)
    destination = TravelportLocationCode(value=request.destination)
    routes = [
        TravelportSearchCriteriaFlight(
            departure_date=request.departure_date,
            origin=origin,
            destination=destination,
        )
    ]
    if request.return_date is not None:
        routes.append(
            TravelportSearchCriteriaFlight(
                departure_date=request.return_date,
                origin=destination,
                destination=origin,
            )
        )

    return routes


def build_travelport_search_modifiers(
    request: FlightSearchInput,
) -> TravelportSearchModifiersAir:
    """Translate normalized preferences into Travelport modifiers."""

    connection_preferences = None

    if request.nonstop_only:
        connection_preferences = [
            TravelportConnectionPreference(
                flight_type=TravelportFlightType(),
            ),
        ]

    return TravelportSearchModifiersAir(
        cabin_preferences=[
            TravelportCabinPreference(
                cabins=[_TRAVELPORT_CABINS[request.cabin_class]],
            ),
        ],
        connection_preferences=connection_preferences,
    )


def build_travelport_search_request(
    request: FlightSearchInput,
) -> TravelportFlightSearchQuery:
    """Assemble a complete Travelport search from normalized input."""

    return TravelportFlightSearchQuery(
        request=TravelportFlightSearchRequest(
            content_source_list=["NDC"],
            offers_per_page=request.max_results,
            passengers=build_travelport_passengers(request),
            routes=build_travelport_routes(request),
            search_modifiers=build_travelport_search_modifiers(request),
            pricing_modifiers=TravelportPricingModifiersAir(
                currency_code=request.currency,
            ),
        ),
    )
