"""Validation of Travelport passenger criteria."""

import pytest
from pydantic import ValidationError

from app.providers.travelport.flight_request_schemas import TravelportPassengerCriteria


@pytest.mark.parametrize(
    ("code", "age"),
    [
        ("ADT", None),
        ("CNN", 2),
        ("CNN", 17),
        ("INF", 0),
        ("INF", 1),
        ("INS", 0),
        ("INS", 1),
    ],
)
def test_valid_category_age_combinations_round_trip(code, age):
    item = TravelportPassengerCriteria(number=1, passenger_type_code=code, age=age)
    assert (
        TravelportPassengerCriteria.model_validate(item.model_dump(by_alias=True))
        == item
    )


@pytest.mark.parametrize(
    ("code", "age"),
    [
        ("ADT", 5),
        ("CNN", None),
        ("CNN", 1),
        ("CNN", 18),
        ("INF", None),
        ("INF", 2),
        ("INS", None),
        ("INS", 2),
        ("INF", -1),
    ],
)
def test_invalid_category_age_combinations_fail(code, age):
    with pytest.raises(ValidationError):
        TravelportPassengerCriteria(number=1, passenger_type_code=code, age=age)


@pytest.mark.parametrize(
    "overrides",
    [
        {"number": 0},
        {"number": 10},
        {"passenger_type_code": "UNKNOWN"},
        {"unexpected": True},
        {"type_": "Other"},
    ],
)
def test_invalid_wire_fields_fail(overrides):
    with pytest.raises(ValidationError):
        TravelportPassengerCriteria.model_validate(
            {"number": 1, "passenger_type_code": "ADT", **overrides}
        )
