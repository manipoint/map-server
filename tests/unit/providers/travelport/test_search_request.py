"""Complete Travelport request wire contract and cross-field validation."""

import pytest
from pydantic import ValidationError

from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.flight_request_mapper import (
    build_travelport_search_request,
)
from app.providers.travelport.flight_request_schemas import TravelportFlightSearchQuery


def payload(**overrides):
    search = FlightSearchInput(
        origin="LHE", destination="NRT", departure_date="2027-11-07", **overrides
    )
    return build_travelport_search_request(search).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


def test_exact_one_way_request_and_json_round_trip():
    result = payload(currency=" pkr ", max_results=3)
    assert result == {
        "@type": "CatalogProductOfferingsQueryRequest",
        "CatalogProductOfferingsRequest": {
            "@type": "CatalogProductOfferingsRequestAir",
            "contentSourceList": ["NDC"],
            "offersPerPage": 3,
            "maxNumberOfUpsellsToReturn": 0,
            "PassengerCriteria": [
                {"@type": "PassengerCriteria", "number": 1, "passengerTypeCode": "ADT"}
            ],
            "SearchCriteriaFlight": [
                {
                    "@type": "SearchCriteriaFlight",
                    "departureDate": "2027-11-07",
                    "From": {"value": "LHE"},
                    "To": {"value": "NRT"},
                }
            ],
            "SearchModifiersAir": {
                "@type": "SearchModifiersAir",
                "CabinPreference": [
                    {
                        "@type": "CabinPreference",
                        "preferenceType": "Permitted",
                        "cabins": ["Economy"],
                    }
                ],
            },
            "PricingModifiersAir": {
                "@type": "PricingModifiersAir",
                "currencyCode": "PKR",
            },
        },
    }
    parsed = TravelportFlightSearchQuery.model_validate(result)
    assert (
        TravelportFlightSearchQuery.model_validate_json(
            parsed.model_dump_json(by_alias=True)
        )
        == parsed
    )


@pytest.mark.parametrize("return_date", ["2027-11-07", "2027-11-11"])
def test_round_trip_group_and_nonstop_are_preserved(return_date):
    query = TravelportFlightSearchQuery.model_validate(
        payload(
            return_date=return_date,
            adults=2,
            children_ages=[4, 9],
            nonstop_only=True,
            cabin_class="business",
            max_results=10,
        )
    )
    body = query.request
    assert sum(p.number for p in body.passengers) == 4
    assert body.routes[1].origin.value == "NRT"
    assert body.routes[1].destination.value == "LHE"
    assert body.routes[1].departure_date.isoformat() == return_date
    assert body.search_modifiers.cabin_preferences[0].cabins == ["Business"]
    assert body.search_modifiers.connection_preferences is not None
    assert body.offers_per_page == 10


@pytest.mark.parametrize(
    "passengers",
    [
        [{"number": 1, "passengerTypeCode": "CNN", "age": 4}],
        [
            {"number": 1, "passengerTypeCode": "ADT"},
            {"number": 2, "passengerTypeCode": "INF", "age": 1},
        ],
        [
            {"number": 9, "passengerTypeCode": "ADT"},
            {"number": 1, "passengerTypeCode": "CNN", "age": 4},
        ],
        [],
    ],
)
def test_direct_wire_input_rejects_invalid_passenger_totals(passengers):
    data = payload()
    data["CatalogProductOfferingsRequest"]["PassengerCriteria"] = passengers
    with pytest.raises(ValidationError):
        TravelportFlightSearchQuery.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("offersPerPage", 0),
        ("offersPerPage", 11),
        ("contentSourceList", []),
        ("contentSourceList", ["GDS"]),
        ("contentSourceList", ["NDC", "NDC"]),
        ("maxNumberOfUpsellsToReturn", 1),
        ("unknown", True),
        ("PricingModifiersAir", {"currencyCode": "US"}),
        ("SearchCriteriaFlight", []),
    ],
)
def test_invalid_request_options_are_rejected(field, value):
    data = payload()
    data["CatalogProductOfferingsRequest"][field] = value
    with pytest.raises(ValidationError):
        TravelportFlightSearchQuery.model_validate(data)


@pytest.mark.parametrize("change", ["route", "date", "extra_leg"])
def test_direct_wire_input_rejects_invalid_return_journey(change):
    data = payload(return_date="2027-11-11")
    routes = data["CatalogProductOfferingsRequest"]["SearchCriteriaFlight"]
    if change == "route":
        routes[1]["To"] = {"value": "KHI"}
    elif change == "date":
        routes[1]["departureDate"] = "2027-11-06"
    else:
        routes.append(routes[0].copy())
    with pytest.raises(ValidationError):
        TravelportFlightSearchQuery.model_validate(data)


def test_mapper_rejects_group_booking_instead_of_truncating():
    with pytest.raises(ValueError, match="at most 9"):
        payload(adults=10)
