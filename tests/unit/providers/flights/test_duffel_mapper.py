"""Tests for mapping normalized flight searches to Duffel payloads."""

from datetime import date

from app.domain.flights import FlightCabinClass
from app.providers.flights.duffel_mapper import build_duffel_offer_request
from app.providers.flights.schemas import FlightSearchInput


def create_search(**overrides: object) -> FlightSearchInput:
    """Create one valid normalized flight search with optional overrides."""

    values: dict[str, object] = {
        "origin": "LHE",
        "destination": "DXB",
        "departure_date": date(2026, 9, 10),
    }
    values.update(overrides)
    return FlightSearchInput(**values)


def test_mapper_builds_minimal_one_way_payload() -> None:
    """Optional provider filters should be omitted from a basic search."""

    payload = build_duffel_offer_request(create_search())

    assert payload.model_dump(mode="json", exclude_none=True) == {
        "data": {
            "slices": [
                {
                    "origin": "LHE",
                    "destination": "DXB",
                    "departure_date": "2026-09-10",
                }
            ],
            "passengers": [{"type": "adult"}],
            "cabin_class": "economy",
        }
    }


def test_mapper_preserves_every_passenger_category() -> None:
    """Children and seated/lap infants must never disappear during mapping."""

    request = create_search(
        adults=2,
        children_ages=[8, 15],
        infants_with_seat_ages=[1],
        infants_on_lap_ages=[0, 1],
    )

    payload = build_duffel_offer_request(request)

    assert [
        passenger.model_dump(exclude_none=True) for passenger in payload.data.passengers
    ] == [
        {"type": "adult"},
        {"type": "adult"},
        {"age": 8},
        {"age": 15},
        {"age": 1},
        {"type": "infant_without_seat"},
        {"type": "infant_without_seat"},
    ]
    assert len(payload.data.passengers) == request.total_travelers


def test_mapper_supports_single_parent_with_seated_and_lap_twins() -> None:
    """One adult may travel with one seated twin and one lap twin."""

    payload = build_duffel_offer_request(
        create_search(
            adults=1,
            infants_with_seat_ages=[1],
            infants_on_lap_ages=[1],
        )
    )

    assert [
        passenger.model_dump(exclude_none=True) for passenger in payload.data.passengers
    ] == [
        {"type": "adult"},
        {"age": 1},
        {"type": "infant_without_seat"},
    ]


def test_mapper_builds_reverse_return_slice() -> None:
    """Round trips should produce a chronological reverse second slice."""

    payload = build_duffel_offer_request(create_search(return_date=date(2026, 9, 15)))

    assert [
        flight_slice.model_dump(mode="json") for flight_slice in payload.data.slices
    ] == [
        {
            "origin": "LHE",
            "destination": "DXB",
            "departure_date": "2026-09-10",
        },
        {
            "origin": "DXB",
            "destination": "LHE",
            "departure_date": "2026-09-15",
        },
    ]


def test_mapper_translates_nonstop_filter_to_zero_connections() -> None:
    """Duffel represents nonstop-only searches using max_connections zero."""

    payload = build_duffel_offer_request(create_search(nonstop_only=True))

    assert payload.data.max_connections == 0


def test_mapper_does_not_apply_an_arbitrary_group_limit() -> None:
    """Large groups should reach the provider boundary without being split."""

    request = create_search(adults=35)

    payload = build_duffel_offer_request(request)

    assert len(payload.data.passengers) == 35


def test_mapper_does_not_send_non_duffel_search_preferences_in_body() -> None:
    """Currency and result limit belong to later response handling, not this body."""

    payload = build_duffel_offer_request(
        create_search(
            currency="PKR",
            max_results=3,
            cabin_class=FlightCabinClass.BUSINESS,
        )
    )
    body = payload.model_dump(mode="json", exclude_none=True)["data"]

    assert body["cabin_class"] == "business"
    assert "currency" not in body
    assert "max_results" not in body
