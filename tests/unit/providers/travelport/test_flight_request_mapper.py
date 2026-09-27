"""Passenger mapping preserves traveler counts, categories and ages."""

from datetime import date

import pytest

from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.flight_request_mapper import build_travelport_passengers


def request(**overrides):
    return FlightSearchInput(
        origin="LHE",
        destination="NRT",
        departure_date=date(2027, 11, 7),
        **overrides,
    )


def serialize(search):
    return [
        p.model_dump(by_alias=True, exclude_none=True)
        for p in build_travelport_passengers(search)
    ]


def test_adults_use_exact_wire_keys_and_omit_age():
    assert serialize(request(adults=2)) == [
        {"@type": "PassengerCriteria", "number": 2, "passengerTypeCode": "ADT"}
    ]


@pytest.mark.parametrize(
    "values", [{}, {"adults": 1}, {"adults": 1, "children_ages": [4, 9]}]
)
def test_default_and_minimum_adult_are_always_included(values):
    search = request(**values)
    result = serialize(search)
    assert result[0] == {
        "@type": "PassengerCriteria",
        "number": 1,
        "passengerTypeCode": "ADT",
    }
    assert sum(item["number"] for item in result) == search.total_travelers


def test_groups_same_ages_without_merging_seated_and_lap_infants():
    search = request(
        adults=2,
        children_ages=[9, 4, 9],
        infants_with_seat_ages=[1],
        infants_on_lap_ages=[1, 0],
    )
    before = search.model_dump()
    result = serialize(search)
    assert [(p["passengerTypeCode"], p.get("age"), p["number"]) for p in result] == [
        ("ADT", None, 2),
        ("CNN", 4, 1),
        ("CNN", 9, 2),
        ("INS", 1, 1),
        ("INF", 0, 1),
        ("INF", 1, 1),
    ]
    assert sum(p["number"] for p in result) == search.total_travelers
    assert search.model_dump() == before


@pytest.mark.parametrize("age", [2, 11, 12, 17])
def test_child_age_is_preserved_for_provider_pricing(age):
    assert serialize(request(children_ages=[age]))[1]["age"] == age


@pytest.mark.parametrize(
    "values", [{"adults": 9}, {"adults": 7, "children_ages": [4, 9]}]
)
def test_nine_travelers_are_supported(values):
    assert sum(p["number"] for p in serialize(request(**values))) == 9


@pytest.mark.parametrize(
    "values",
    [
        {"adults": 10},
        {"adults": 8, "children_ages": [4, 9]},
        {"adults": 9, "infants_on_lap_ages": [0]},
    ],
)
def test_group_requests_are_rejected_without_truncation(values):
    with pytest.raises(ValueError, match="at most 9"):
        build_travelport_passengers(request(**values))
