"""Tests for normalized flight-provider response models."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.flights import FlightSearchStatus
from app.providers.flights.schemas import (
    FlightItinerary,
    FlightOffer,
    FlightSearchResult,
    FlightSegment,
)


def create_segment(**overrides: object) -> FlightSegment:
    """Create one valid flight segment with optional overrides."""

    values: dict[str, object] = {
        "departure_airport": "LHE",
        "arrival_airport": "KHI",
        "departure_at": datetime(2026, 9, 10, 8, tzinfo=UTC),
        "arrival_at": datetime(2026, 9, 10, 10, tzinfo=UTC),
        "departure_time_zone": "Asia/Karachi",
        "arrival_time_zone": "Asia/Karachi",
        "marketing_carrier_code": "PK",
        "marketing_carrier_name": "Pakistan International Airlines",
        "marketing_flight_number": "303",
        "operating_carrier_code": "PK",
        "operating_carrier_name": "Pakistan International Airlines",
        "operating_flight_number": "303",
        "duration_minutes": 120,
    }
    values.update(overrides)
    return FlightSegment(**values)


def create_offer(**overrides: object) -> FlightOffer:
    """Create one valid whole-party flight offer with optional overrides."""

    values: dict[str, object] = {
        "offer_id": "offer-1",
        "outbound": FlightItinerary(
            segments=[create_segment()],
            duration_minutes=120,
        ),
        "total_price": "72500.50",
        "currency": "PKR",
        "traveler_count": 3,
    }
    values.update(overrides)
    return FlightOffer(**values)


def test_segment_normalizes_codes_and_accepts_timezone_aware_times() -> None:
    """Provider codes should normalize without discarding timezones."""

    segment = create_segment(
        departure_airport=" lhe ",
        arrival_airport=" khi ",
        marketing_carrier_code=" pk ",
        marketing_carrier_name=" Pakistan International Airlines ",
        marketing_flight_number=" 303 ",
        operating_carrier_code=" pa ",
        operating_carrier_name=" Partner Airline ",
        operating_flight_number=" pa417 ",
    )

    assert segment.departure_airport == "LHE"
    assert segment.arrival_airport == "KHI"
    assert segment.marketing_carrier_code == "PK"
    assert segment.marketing_carrier_name == "Pakistan International Airlines"
    assert segment.marketing_flight_number == "303"
    assert segment.operating_carrier_code == "PA"
    assert segment.operating_carrier_name == "Partner Airline"
    assert segment.operating_flight_number == "PA417"
    assert segment.departure_at.utcoffset() == timedelta(0)
    assert segment.departure_time_zone == "Asia/Karachi"
    assert segment.arrival_time_zone == "Asia/Karachi"


def test_segment_accepts_provider_local_naive_times() -> None:
    """Provider-local schedules may legitimately omit timezone offsets."""

    segment = create_segment(
        departure_at=datetime(2026, 9, 10, 8),
        arrival_at=datetime(2026, 9, 10, 10),
    )

    assert segment.departure_at.utcoffset() is None
    assert segment.arrival_at.utcoffset() is None


@pytest.mark.parametrize(
    ("departure_at", "arrival_at"),
    [
        (
            datetime(2026, 9, 10, 8),
            datetime(2026, 9, 10, 10, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 10, 8, tzinfo=UTC),
            datetime(2026, 9, 10, 10),
        ),
    ],
)
def test_segment_rejects_mixed_timezone_information(
    departure_at: datetime,
    arrival_at: datetime,
) -> None:
    """One segment must not mix local-naive and timezone-aware timestamps."""

    with pytest.raises(ValidationError, match="consistent timezone information"):
        create_segment(
            departure_at=departure_at,
            arrival_at=arrival_at,
        )


def test_segment_does_not_compare_provider_local_times_across_airports() -> None:
    """Local clock order may differ when a route crosses timezone boundaries."""

    segment = create_segment(
        departure_at=datetime(2026, 9, 10, 23, 30),
        arrival_at=datetime(2026, 9, 10, 22, 30),
    )

    assert segment.duration_minutes == 120


@pytest.mark.parametrize(
    "arrival_at",
    [
        datetime(2026, 9, 10, 8, tzinfo=UTC),
        datetime(2026, 9, 10, 7, 59, tzinfo=UTC),
    ],
)
def test_segment_rejects_non_chronological_arrival(arrival_at: datetime) -> None:
    """A segment must arrive strictly after it departs."""

    with pytest.raises(ValidationError, match="arrival_at must be after departure_at"):
        create_segment(arrival_at=arrival_at)


def test_segment_rejects_identical_airports() -> None:
    """One flight segment cannot begin and end at the same airport."""

    with pytest.raises(ValidationError, match="segment airports must be different"):
        create_segment(arrival_airport="LHE")


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("marketing_carrier_code", "P@"),
        ("marketing_flight_number", "PK-303"),
        ("operating_carrier_code", "P@"),
        ("operating_flight_number", "PK-303"),
        ("marketing_carrier_name", " "),
        ("operating_carrier_name", " "),
        ("departure_time_zone", " "),
        ("arrival_time_zone", " "),
        ("duration_minutes", 0),
    ],
)
def test_segment_rejects_invalid_provider_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    """Malformed provider identifiers and durations should be rejected."""

    with pytest.raises(ValidationError):
        create_segment(**{field_name: invalid_value})


def test_itinerary_computes_direct_and_connecting_stops() -> None:
    """Stops should count connections rather than flight segments."""

    direct = FlightItinerary(segments=[create_segment()], duration_minutes=120)
    connection = create_segment(
        departure_airport="KHI",
        arrival_airport="DXB",
        departure_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
        arrival_at=datetime(2026, 9, 10, 14, tzinfo=UTC),
    )
    connecting = FlightItinerary(
        segments=[create_segment(), connection],
        duration_minutes=360,
    )

    assert direct.stops == 0
    assert connecting.stops == 1
    assert direct.model_dump()["stops"] == 0


def test_offer_preserves_decimal_group_total_and_normalizes_currency() -> None:
    """The quoted price should remain an exact total for all travelers."""

    offer = create_offer(currency=" pkr ")

    assert offer.total_price == Decimal("72500.50")
    assert offer.currency == "PKR"
    assert offer.traveler_count == 3
    assert offer.return_itinerary is None


def test_offer_accepts_timezone_aware_expiry() -> None:
    """A Duffel expiry should remain an absolute book-before instant."""

    expires_at = datetime(2026, 9, 10, 7, 30, tzinfo=UTC)

    offer = create_offer(expires_at=expires_at)

    assert offer.expires_at == expires_at


def test_offer_rejects_naive_expiry() -> None:
    """An expiry without timezone information is unsafe for booking decisions."""

    with pytest.raises(ValidationError, match="expires_at must include a timezone"):
        create_offer(expires_at=datetime(2026, 9, 10, 7, 30))


def test_available_result_requires_at_least_one_offer() -> None:
    """An available status without any offers is internally inconsistent."""

    with pytest.raises(ValidationError, match="requires at least one offer"):
        FlightSearchResult(
            status=FlightSearchStatus.OFFERS_AVAILABLE,
            searched_at=datetime.now(UTC),
        )


def test_available_result_accepts_normalized_offers() -> None:
    """A successful provider result should retain its bounded offer list."""

    result = FlightSearchResult(
        status=FlightSearchStatus.OFFERS_AVAILABLE,
        searched_at=datetime.now(UTC),
        offers=[create_offer()],
    )

    assert len(result.offers) == 1


def test_no_offers_result_accepts_an_empty_offer_list() -> None:
    """No availability is a valid provider outcome rather than an exception."""

    result = FlightSearchResult(
        status=FlightSearchStatus.NO_OFFERS,
        searched_at=datetime.now(UTC),
        message="No matching flights were found.",
    )

    assert result.offers == []


def test_non_offer_result_rejects_offers() -> None:
    """Offers must not conflict with a non-availability status."""

    with pytest.raises(ValidationError, match="cannot contain offers"):
        FlightSearchResult(
            status=FlightSearchStatus.NO_OFFERS,
            searched_at=datetime.now(UTC),
            offers=[create_offer()],
        )


@pytest.mark.parametrize("message", [None, "", "   "])
def test_group_booking_result_requires_actionable_guidance(
    message: str | None,
) -> None:
    """Large groups should receive guidance for completing their booking."""

    with pytest.raises(ValidationError, match="requires user guidance"):
        FlightSearchResult(
            status=FlightSearchStatus.GROUP_BOOKING_REQUIRED,
            searched_at=datetime.now(UTC),
            message=message,
        )


def test_group_booking_result_is_a_valid_empty_offer_outcome() -> None:
    """A provider group limit should not become a generic system failure."""

    result = FlightSearchResult(
        status=FlightSearchStatus.GROUP_BOOKING_REQUIRED,
        searched_at=datetime.now(UTC),
        message="Contact the airline group desk for a quote for 12 travelers.",
    )

    assert result.offers == []


def test_search_result_rejects_naive_search_timestamp() -> None:
    """Result freshness cannot be interpreted safely without a timezone."""

    with pytest.raises(ValidationError, match="searched_at must include a timezone"):
        FlightSearchResult(
            status=FlightSearchStatus.NO_OFFERS,
            searched_at=datetime(2026, 9, 10, 8),
        )


def test_search_result_limits_provider_offers_to_ten() -> None:
    """Provider payloads should stay within the MCP and LLM cost boundary."""

    with pytest.raises(ValidationError):
        FlightSearchResult(
            status=FlightSearchStatus.OFFERS_AVAILABLE,
            searched_at=datetime.now(UTC),
            offers=[create_offer(offer_id=f"offer-{index}") for index in range(11)],
        )
