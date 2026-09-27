"""Route mapping and wire validation without provider calls."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.flight_request_mapper import build_travelport_routes
from app.providers.travelport.flight_request_schemas import (
    TravelportLocationCode,
    TravelportSearchCriteriaFlight,
)


@pytest.mark.parametrize("return_date", [None, date(2028, 2, 29), date(2028, 3, 2)])
def test_routes_preserve_dates_and_reverse_return_airports(return_date):
    search = FlightSearchInput(
        origin=" lhe ",
        destination="nrt",
        departure_date=date(2028, 2, 29),
        return_date=return_date,
    )
    before = search.model_dump()
    routes = build_travelport_routes(search)
    payload = [route.model_dump(mode="json", by_alias=True) for route in routes]
    expected = [
        {
            "@type": "SearchCriteriaFlight",
            "departureDate": "2028-02-29",
            "From": {"value": "LHE"},
            "To": {"value": "NRT"},
        }
    ]
    if return_date is not None:
        expected.append(
            {
                "@type": "SearchCriteriaFlight",
                "departureDate": return_date.isoformat(),
                "From": {"value": "NRT"},
                "To": {"value": "LHE"},
            }
        )
    assert payload == expected
    assert search.model_dump() == before
    assert [TravelportSearchCriteriaFlight.model_validate(p) for p in payload] == routes


@pytest.mark.parametrize("code", ["", "LH", "LHES", "lhe", "LH1", " LHE", None])
def test_wire_location_rejects_invalid_codes(code):
    with pytest.raises(ValidationError):
        TravelportLocationCode(value=code)


@pytest.mark.parametrize(
    "overrides",
    [
        {"To": {"value": "LHE"}},
        {"departureDate": "2027-02-29"},
        {"departureDate": "not-a-date"},
        {"@type": "Other"},
        {"unknown": True},
        {"From": {"value": "LHE", "unknown": True}},
    ],
)
def test_invalid_route_payload_is_rejected(overrides):
    with pytest.raises(ValidationError):
        TravelportSearchCriteriaFlight.model_validate(
            {
                "departureDate": "2028-02-29",
                "From": {"value": "LHE"},
                "To": {"value": "NRT"},
                **overrides,
            }
        )


def test_return_before_departure_is_rejected_at_shared_boundary():
    with pytest.raises(ValidationError, match="return_date"):
        FlightSearchInput(
            origin="LHE",
            destination="NRT",
            departure_date="2028-03-02",
            return_date="2028-03-01",
        )
