"""Tests for provider-independent flight-search input."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightCabinClass
from app.mcp.schemas.flights import FlightSearchGuidance, FlightSearchInput


def create_search(**overrides: object) -> FlightSearchInput:
    """Create one valid flight search with optional field overrides."""

    values: dict[str, object] = {
        "origin": "LHE",
        "destination": "KHI",
        "departure_date": date(2026, 9, 10),
    }
    values.update(overrides)
    return FlightSearchInput(**values)


def test_flight_search_normalizes_codes_and_uses_cost_aware_defaults() -> None:
    """Codes should normalize while optional search fields remain bounded."""

    search = create_search(
        origin=" lhe ",
        destination=" khi ",
        currency=" pkr ",
    )

    assert search.origin == "LHE"
    assert search.destination == "KHI"
    assert search.currency == "PKR"
    assert search.cabin_class is FlightCabinClass.ECONOMY
    assert search.nonstop_only is False
    assert search.max_results == 5


@pytest.mark.parametrize("adults", [12, 35])
def test_flight_search_accepts_real_world_groups(adults: int) -> None:
    """Provider booking limits must not reject friends or office groups."""

    search = create_search(adults=adults)

    assert search.adults == adults
    assert search.total_travelers == adults


def test_single_parent_can_travel_with_twins_using_one_infant_seat() -> None:
    """One lap infant and one seated infant should be valid for one adult."""

    search = create_search(
        adults=1,
        infants_on_lap_ages=[1],
        infants_with_seat_ages=[1],
    )

    assert search.total_travelers == 3


def test_two_adults_can_each_accompany_one_lap_infant() -> None:
    """The lap-infant relationship should apply per accompanying adult."""

    search = create_search(adults=2, infants_on_lap_ages=[0, 1])

    assert search.total_travelers == 4


def test_excess_lap_infants_receive_an_actionable_validation_error() -> None:
    """A solo adult with two lap infants should be advised to book a seat."""

    with pytest.raises(
        ValidationError,
        match="book additional infants with their own seat",
    ):
        create_search(adults=1, infants_on_lap_ages=[0, 1])


def test_flight_search_rejects_the_same_origin_and_destination() -> None:
    """A flight route must contain two different IATA codes."""

    with pytest.raises(
        ValidationError,
        match="origin and destination must be different",
    ):
        create_search(destination="LHE")


def test_flight_search_rejects_return_before_departure() -> None:
    """A round trip cannot return before its outbound departure."""

    with pytest.raises(
        ValidationError,
        match="return_date must be on or after departure_date",
    ):
        create_search(return_date=date(2026, 9, 9))


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("origin", "LAHR"),
        ("destination", "12A"),
        ("currency", "US"),
        ("currency", "123"),
        ("adults", 0),
        ("children_ages", [1]),
        ("children_ages", [18]),
        ("infants_with_seat_ages", [-1]),
        ("infants_with_seat_ages", [2]),
        ("infants_on_lap_ages", [-1]),
        ("infants_on_lap_ages", [2]),
        ("children", 1),
        ("infants_with_seat", 1),
        ("infants_on_lap", 1),
        ("max_results", 0),
        ("max_results", 11),
    ],
)
def test_flight_search_rejects_invalid_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    """Malformed codes, counts, and expensive result sizes should be rejected."""

    with pytest.raises(ValidationError):
        create_search(**{field_name: invalid_value})


def test_flight_search_accepts_round_trip_and_requested_cabin() -> None:
    """A valid return date and cabin class should survive normalization."""

    search = create_search(
        return_date=date(2026, 9, 15),
        cabin_class="business",
        adults=2,
        children_ages=[8],
    )

    assert search.return_date == date(2026, 9, 15)
    assert search.cabin_class is FlightCabinClass.BUSINESS
    assert search.total_travelers == 3


def test_flight_search_accepts_passenger_age_boundaries() -> None:
    """Supported child and infant age endpoints should remain valid."""

    search = create_search(
        children_ages=[2, 17],
        infants_with_seat_ages=[0],
        infants_on_lap_ages=[1],
    )

    assert search.total_travelers == 5


def test_flight_search_guidance_normalizes_safe_invalid_date_response() -> None:
    """Date failures should serialize as one compact structured outcome."""

    guidance = FlightSearchGuidance(
        status="invalid_dates",
        message="  Flight departure date cannot be in the past  ",
    )

    assert guidance.model_dump() == {
        "status": "invalid_dates",
        "message": "Flight departure date cannot be in the past",
    }


@pytest.mark.parametrize(
    "values",
    [
        {"status": "provider_error", "message": "Try again later"},
        {"status": "invalid_dates", "message": "   "},
        {"status": "invalid_dates", "message": "x" * 501},
        {
            "status": "invalid_dates",
            "message": "Invalid date",
            "details": "internal data",
        },
    ],
)
def test_flight_search_guidance_rejects_unsupported_or_unbounded_input(
    values: dict[str, object],
) -> None:
    """Guidance should expose only its documented bounded public contract."""

    with pytest.raises(ValidationError):
        FlightSearchGuidance.model_validate(values)
