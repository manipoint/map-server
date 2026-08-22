"""Tests for provider-independent flight-search input."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightCabinClass
from app.mcp.schemas.flights import FlightSearchInput


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
        infants_on_lap=1,
        infants_with_seat=1,
    )

    assert search.total_travelers == 3


def test_two_adults_can_each_accompany_one_lap_infant() -> None:
    """The lap-infant relationship should apply per accompanying adult."""

    search = create_search(adults=2, infants_on_lap=2)

    assert search.total_travelers == 4


def test_excess_lap_infants_receive_an_actionable_validation_error() -> None:
    """A solo adult with two lap infants should be advised to book a seat."""

    with pytest.raises(
        ValidationError,
        match="book additional infants with their own seat",
    ):
        create_search(adults=1, infants_on_lap=2)


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
        ("children", -1),
        ("infants_with_seat", -1),
        ("infants_on_lap", -1),
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
        children=1,
    )

    assert search.return_date == date(2026, 9, 15)
    assert search.cabin_class is FlightCabinClass.BUSINESS
    assert search.total_travelers == 3
