"""Tests for validated Duffel API request schemas."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightCabinClass
from app.providers.flights.duffel_schemas import (
    DuffelOfferRequestData,
    DuffelOfferRequestPayload,
    DuffelPassenger,
    DuffelSlice,
)


def create_slice(**overrides: object) -> DuffelSlice:
    """Create a valid outbound Duffel slice with optional overrides."""

    values: dict[str, object] = {
        "origin": "LHE",
        "destination": "DXB",
        "departure_date": date(2026, 9, 10),
    }
    values.update(overrides)
    return DuffelSlice(**values)


def create_request_data(**overrides: object) -> DuffelOfferRequestData:
    """Create valid Duffel offer-request data with optional overrides."""

    values: dict[str, object] = {
        "slices": [create_slice()],
        "passengers": [DuffelPassenger(type="adult")],
        "cabin_class": FlightCabinClass.ECONOMY,
    }
    values.update(overrides)
    return DuffelOfferRequestData(**values)


@pytest.mark.parametrize("passenger_type", ["adult", "child", "infant_without_seat"])
def test_duffel_passenger_accepts_supported_types(passenger_type: str) -> None:
    """Duffel passenger classifications should serialize without translation."""

    passenger = DuffelPassenger(type=passenger_type)

    assert passenger.model_dump(exclude_none=True) == {"type": passenger_type}


@pytest.mark.parametrize("age", [0, 1, 13, 18, 120])
def test_duffel_passenger_accepts_documented_age_representation(age: int) -> None:
    """An exact age should be preserved, including an age of zero."""

    passenger = DuffelPassenger(age=age)

    assert passenger.model_dump(exclude_none=True) == {"age": age}


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"type": "adult", "age": 30},
    ],
)
def test_duffel_passenger_requires_exactly_one_classification(
    values: dict[str, object],
) -> None:
    """Ambiguous or absent passenger classification should be rejected."""

    with pytest.raises(ValidationError, match="exactly one of type or age"):
        DuffelPassenger(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"type": "senior"},
        {"age": -1},
        {"age": 121},
        {"type": "adult", "unknown": True},
    ],
)
def test_duffel_passenger_rejects_invalid_fields(values: dict[str, object]) -> None:
    """Unsupported classifications, ages, and fields should fail validation."""

    with pytest.raises(ValidationError):
        DuffelPassenger(**values)


def test_duffel_slice_normalizes_airport_codes() -> None:
    """Outgoing Duffel routes should contain uppercase IATA codes."""

    flight_slice = create_slice(origin=" lhe ", destination=" dxb ")

    assert flight_slice.origin == "LHE"
    assert flight_slice.destination == "DXB"


def test_duffel_slice_rejects_identical_airports() -> None:
    """A provider slice cannot start and end at the same airport."""

    with pytest.raises(ValidationError, match="airports must be different"):
        create_slice(destination="LHE")


def test_duffel_request_accepts_chronological_round_trip() -> None:
    """A return slice should reverse the outbound route."""

    request = create_request_data(
        slices=[
            create_slice(),
            create_slice(
                origin="DXB",
                destination="LHE",
                departure_date=date(2026, 9, 15),
            ),
        ]
    )

    assert len(request.slices) == 2


def test_duffel_request_rejects_non_reversing_return_slice() -> None:
    """A round-trip response must not accidentally search a different route."""

    with pytest.raises(ValidationError, match="must reverse the outbound route"):
        create_request_data(
            slices=[
                create_slice(),
                create_slice(
                    origin="AUH",
                    destination="LHE",
                    departure_date=date(2026, 9, 15),
                ),
            ]
        )


def test_duffel_request_rejects_return_before_departure() -> None:
    """A return search cannot precede its outbound flight date."""

    with pytest.raises(ValidationError, match="must not precede departure date"):
        create_request_data(
            slices=[
                create_slice(),
                create_slice(
                    origin="DXB",
                    destination="LHE",
                    departure_date=date(2026, 9, 9),
                ),
            ]
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("slices", []),
        ("slices", [create_slice(), create_slice(), create_slice()]),
        ("passengers", []),
        ("max_connections", -1),
    ],
)
def test_duffel_request_rejects_invalid_collection_boundaries(
    field_name: str,
    invalid_value: object,
) -> None:
    """Malformed journey and passenger collections should not reach Duffel."""

    with pytest.raises(ValidationError):
        create_request_data(**{field_name: invalid_value})


def test_duffel_payload_serializes_exact_api_envelope() -> None:
    """Payload JSON should match Duffel's data envelope without null fields."""

    payload = DuffelOfferRequestPayload(
        data=create_request_data(
            passengers=[
                DuffelPassenger(type="adult"),
                DuffelPassenger(age=8),
                DuffelPassenger(type="infant_without_seat"),
            ],
            cabin_class=FlightCabinClass.BUSINESS,
            max_connections=0,
        )
    )

    assert payload.model_dump(mode="json", exclude_none=True) == {
        "data": {
            "slices": [
                {
                    "origin": "LHE",
                    "destination": "DXB",
                    "departure_date": "2026-09-10",
                }
            ],
            "passengers": [
                {"type": "adult"},
                {"age": 8},
                {"type": "infant_without_seat"},
            ],
            "cabin_class": "business",
            "max_connections": 0,
        }
    }
