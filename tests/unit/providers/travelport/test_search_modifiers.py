"""Cabin and nonstop preferences must survive Travelport serialization."""

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightCabinClass
from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.flight_request_mapper import (
    build_travelport_search_modifiers,
)
from app.providers.travelport.flight_request_schemas import (
    TravelportCabinPreference,
    TravelportConnectionPreference,
    TravelportFlightType,
    TravelportSearchModifiersAir,
)


@pytest.mark.parametrize(
    ("cabin", "wire_cabin"),
    [
        (FlightCabinClass.ECONOMY, "Economy"),
        (FlightCabinClass.PREMIUM_ECONOMY, "PremiumEconomy"),
        (FlightCabinClass.BUSINESS, "Business"),
        (FlightCabinClass.FIRST, "First"),
    ],
)
@pytest.mark.parametrize("nonstop", [False, True])
def test_exact_modifier_payload_and_round_trip(cabin, wire_cabin, nonstop):
    request = FlightSearchInput(
        origin="LHE",
        destination="NRT",
        departure_date="2027-11-07",
        cabin_class=cabin,
        nonstop_only=nonstop,
    )
    before = request.model_dump()
    result = build_travelport_search_modifiers(request)
    payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    expected = {
        "@type": "SearchModifiersAir",
        "CabinPreference": [
            {
                "@type": "CabinPreference",
                "preferenceType": "Permitted",
                "cabins": [wire_cabin],
            }
        ],
    }
    if nonstop:
        expected["ConnectionPreferences"] = [
            {
                "@type": "ConnectionPreferencesAir",
                "FlightType": {"connectionType": "NonStopDirect"},
            }
        ]
    assert payload == expected
    assert TravelportSearchModifiersAir.model_validate(payload) == result
    assert request.model_dump() == before


@pytest.mark.parametrize(
    "cabins", [[], ["Economy", "Business"], ["economy"], ["Unknown"]]
)
def test_invalid_cabin_selection_is_rejected(cabins):
    with pytest.raises(ValidationError):
        TravelportCabinPreference(cabins=cabins)


@pytest.mark.parametrize("field", ["cabin_preferences", "connection_preferences"])
@pytest.mark.parametrize("count", [0, 2])
def test_modifier_collections_require_exactly_one_item_when_present(field, count):
    cabin = TravelportCabinPreference(cabins=["Economy"])
    connection = TravelportConnectionPreference(flight_type=TravelportFlightType())
    values = {"cabin_preferences": [cabin]}
    values[field] = [cabin if field == "cabin_preferences" else connection] * count
    with pytest.raises(ValidationError):
        TravelportSearchModifiersAir.model_validate(values)


def test_connection_discriminator_cannot_be_changed():
    with pytest.raises(ValidationError):
        TravelportConnectionPreference.model_validate(
            {
                "@type": "WrongType",
                "FlightType": {"connectionType": "NonStopDirect"},
            }
        )


def test_nonstop_type_rejects_connecting_flights():
    with pytest.raises(ValidationError):
        TravelportFlightType(connection_type="SingleConnection")
