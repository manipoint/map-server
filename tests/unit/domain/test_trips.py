"""Tests for trip-planning domain models."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightCabinClass
from app.domain.trips import (
    MAX_TRAVELERS_PER_REQUEST,
    TravelerParty,
    TripRequest,
    TripStatus,
    TripUpdate,
)


def test_trip_status_has_only_persisted_lifecycle_values() -> None:
    """Date-derived trip views should not become persisted statuses."""

    assert {status.value for status in TripStatus} == {
        "draft",
        "planned",
        "archived",
    }


def test_traveler_party_defaults_to_one_adult() -> None:
    """A request without passenger details should represent one adult."""

    party = TravelerParty()

    assert party.adults == 1
    assert party.total_travelers == 1


def test_traveler_party_counts_every_traveler_category() -> None:
    """The derived total should include adults, children, and both infant types."""

    party = TravelerParty(
        adults=2,
        children_ages=[3, 12],
        infants_with_seat_ages=[1],
        infants_on_lap_ages=[0],
    )

    assert party.total_travelers == 6
    assert "total_travelers" not in party.model_dump()


def test_traveler_party_accepts_group_larger_than_nine() -> None:
    """Large groups should reach provider-specific group-booking handling."""

    party = TravelerParty(adults=12)

    assert party.total_travelers == 12


def test_traveler_party_accepts_one_parent_with_child_twins() -> None:
    """A parent traveling with twins who are children should be valid."""

    party = TravelerParty(adults=1, children_ages=[3, 3])

    assert party.total_travelers == 3


def test_traveler_party_accepts_additional_infant_with_own_seat() -> None:
    """One adult may travel with one lap infant and another seated infant."""

    party = TravelerParty(
        adults=1,
        infants_on_lap_ages=[0],
        infants_with_seat_ages=[0],
    )

    assert party.total_travelers == 3


def test_traveler_party_rejects_more_lap_infants_than_adults() -> None:
    """Every lap infant should have a distinct accompanying adult."""

    with pytest.raises(
        ValidationError,
        match="each lap infant must be accompanied by one adult",
    ):
        TravelerParty(adults=1, infants_on_lap_ages=[0, 1])


@pytest.mark.parametrize(
    ("field", "age"),
    [
        ("children_ages", 1),
        ("children_ages", 18),
        ("infants_with_seat_ages", 2),
        ("infants_on_lap_ages", -1),
    ],
)
def test_traveler_party_rejects_invalid_ages(field: str, age: int) -> None:
    """Traveler categories should enforce their supported age ranges."""

    with pytest.raises(ValidationError):
        TravelerParty(**{field: [age]})


def test_traveler_party_rejects_total_above_request_limit() -> None:
    """Combined traveler categories should respect the application safety limit."""

    with pytest.raises(
        ValidationError,
        match=f"traveler count cannot exceed {MAX_TRAVELERS_PER_REQUEST}",
    ):
        TravelerParty(
            adults=MAX_TRAVELERS_PER_REQUEST,
            children_ages=[2],
        )


def test_traveler_party_rejects_unknown_fields() -> None:
    """Unexpected input should not silently alter a traveler request."""

    with pytest.raises(ValidationError):
        TravelerParty(adults=1, seniors=2)


def test_trip_request_accepts_minimum_local_trip() -> None:
    """A local itinerary should not require a flight origin."""

    request = TripRequest(
        destination="London, United Kingdom",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
    )

    assert request.origin is None
    assert request.travelers == TravelerParty()
    assert request.rooms == 1
    assert request.budget_currency == "USD"
    assert request.cabin_class is FlightCabinClass.ECONOMY


def test_trip_request_normalizes_text_currency_and_interests() -> None:
    """Text input should be compact and interests should retain first-seen order."""

    request = TripRequest(
        origin=" London ",
        destination=" Paris ",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        interests=[" Museums ", "museums", "Parks", " parks "],
        total_budget=Decimal("1500.00"),
        budget_currency=" eur ",
    )

    assert request.origin == "London"
    assert request.destination == "Paris"
    assert request.interests == ["Museums", "Parks"]
    assert request.total_budget == Decimal("1500.00")
    assert request.budget_currency == "EUR"


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (date(2026, 9, 10), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 9, 10)),
    ],
)
def test_trip_request_requires_end_date_after_start_date(
    start_date: date,
    end_date: date,
) -> None:
    """A trip should contain at least one overnight date interval."""

    with pytest.raises(ValidationError, match="end_date must be after start_date"):
        TripRequest(
            destination="Paris",
            start_date=start_date,
            end_date=end_date,
        )


def test_trip_request_rejects_same_origin_and_destination() -> None:
    """Equivalent route endpoints should be rejected case-insensitively."""

    with pytest.raises(
        ValidationError,
        match="origin and destination must be different",
    ):
        TripRequest(
            origin=" London ",
            destination="london",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
        )


def test_trip_request_requires_one_adult_per_room() -> None:
    """Every requested hotel room should have an accompanying adult."""

    with pytest.raises(
        ValidationError,
        match="each room requires at least one adult",
    ):
        TripRequest(
            destination="Paris",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
            travelers=TravelerParty(adults=1, children_ages=[4, 7]),
            rooms=2,
        )


def test_trip_request_accepts_large_office_group() -> None:
    """Provider group-booking limits should not reject the overall trip request."""

    request = TripRequest(
        origin="London",
        destination="Paris",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        travelers=TravelerParty(adults=12),
        rooms=6,
    )

    assert request.travelers.total_travelers == 12
    assert request.rooms == 6


def test_trip_request_accepts_past_dates_at_domain_layer() -> None:
    """Relative date checks should remain a service-layer responsibility."""

    request = TripRequest(
        destination="Paris",
        start_date=date(2020, 1, 1),
        end_date=date(2020, 1, 2),
    )

    assert request.start_date == date(2020, 1, 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"interests": [" "]},
        {"interests": ["x" * 61]},
        {"interests": [f"interest-{index}" for index in range(11)]},
        {"total_budget": Decimal("0")},
        {"budget_currency": "US"},
        {"unexpected": True},
    ],
)
def test_trip_request_rejects_invalid_bounded_input(
    overrides: dict[str, object],
) -> None:
    """Bounded fields and unknown input should fail before orchestration."""

    with pytest.raises(ValidationError):
        TripRequest(
            destination="Paris",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
            **overrides,
        )


def test_trip_update_preserves_omitted_fields() -> None:
    """A partial update should retain exactly which fields were supplied."""

    update = TripUpdate(destination=" Paris ")

    assert update.destination == "Paris"
    assert update.model_fields_set == {"destination"}
    assert update.model_dump(exclude_unset=True) == {"destination": "Paris"}


def test_trip_update_allows_explicitly_clearing_nullable_fields() -> None:
    """Explicit null should clear title or origin instead of preserving it."""

    update = TripUpdate(title=None, origin=None)

    assert update.model_fields_set == {"title", "origin"}
    assert update.model_dump(exclude_unset=True) == {
        "title": None,
        "origin": None,
    }


def test_trip_update_rejects_empty_payload() -> None:
    """An empty PATCH body should not perform a meaningless database write."""

    with pytest.raises(
        ValidationError,
        match="at least one trip field must be provided",
    ):
        TripUpdate()


@pytest.mark.parametrize("field_name", ["destination", "start_date", "end_date"])
def test_trip_update_rejects_null_required_field(field_name: str) -> None:
    """Required trip details may be omitted but cannot be explicitly cleared."""

    with pytest.raises(ValidationError, match=rf"{field_name} cannot be null"):
        TripUpdate(**{field_name: None})


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (date(2026, 9, 10), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 9, 10)),
    ],
)
def test_trip_update_rejects_invalid_complete_date_range(
    start_date: date,
    end_date: date,
) -> None:
    """A complete date range should be validated before service execution."""

    with pytest.raises(ValidationError, match="end_date must be after start_date"):
        TripUpdate(start_date=start_date, end_date=end_date)


@pytest.mark.parametrize(
    "values",
    [
        {"start_date": date(2026, 9, 10)},
        {"end_date": date(2026, 9, 12)},
    ],
)
def test_trip_update_accepts_one_date_for_service_level_merge(
    values: dict[str, date],
) -> None:
    """A single changed date requires validation against the stored trip."""

    update = TripUpdate(**values)

    assert update.model_dump(exclude_unset=True) == values


@pytest.mark.parametrize(
    "values",
    [
        {"title": " "},
        {"origin": "x"},
        {"destination": "x"},
        {"unexpected": True},
    ],
)
def test_trip_update_rejects_invalid_bounded_or_unknown_input(
    values: dict[str, object],
) -> None:
    """Partial updates should retain the trip field and extra-input bounds."""

    with pytest.raises(ValidationError):
        TripUpdate(**values)
