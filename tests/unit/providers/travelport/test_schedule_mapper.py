"""Local schedule conversion must not guess through DST transitions."""

from datetime import UTC, datetime

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.providers.flights.metadata_schemas import AirportMetadata
from app.providers.travelport.flight_response_schemas import TravelportFlightEndpoint
from app.providers.travelport.flight_schedule_mapper import resolve_flight_endpoint_time


@pytest.mark.parametrize(
    ("zone", "day", "clock", "expected"),
    [
        (
            "Asia/Karachi",
            "2027-01-01",
            "02:00:00",
            datetime(2026, 12, 31, 21, tzinfo=UTC),
        ),
        (
            "Asia/Kathmandu",
            "2027-01-01",
            "12:00:00",
            datetime(2027, 1, 1, 6, 15, tzinfo=UTC),
        ),
        (
            "America/New_York",
            "2026-07-01",
            "12:00:00",
            datetime(2026, 7, 1, 16, tzinfo=UTC),
        ),
        (
            "America/New_York",
            "2026-01-01",
            "12:00:00",
            datetime(2026, 1, 1, 17, tzinfo=UTC),
        ),
    ],
)
def test_valid_local_time_resolves_to_exact_utc(zone, day, clock, expected):
    result = resolve_flight_endpoint_time(
        endpoint=TravelportFlightEndpoint(
            location="AAA", local_date=day, local_time=clock
        ),
        airport=AirportMetadata(iata_code="AAA", time_zone=zone),
    )
    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.parametrize(
    ("day", "clock", "message"),
    [
        ("2026-03-08", "02:30:00", "nonexistent"),
        ("2026-11-01", "01:30:00", "ambiguous"),
    ],
)
def test_dst_gap_and_fold_are_rejected(day, clock, message):
    with pytest.raises(ProviderUnavailableError, match=message):
        resolve_flight_endpoint_time(
            endpoint=TravelportFlightEndpoint(
                location="JFK", local_date=day, local_time=clock
            ),
            airport=AirportMetadata(iata_code="JFK", time_zone="America/New_York"),
        )


def test_mismatched_airport_is_rejected():
    with pytest.raises(ProviderUnavailableError, match="does not match"):
        resolve_flight_endpoint_time(
            endpoint=TravelportFlightEndpoint(
                location="LHE", local_date="2027-01-01", local_time="12:00:00"
            ),
            airport=AirportMetadata(iata_code="JFK", time_zone="America/New_York"),
        )


def test_explicit_offset_is_not_silently_discarded():
    with pytest.raises(ProviderUnavailableError, match="without an offset"):
        resolve_flight_endpoint_time(
            endpoint=TravelportFlightEndpoint(
                location="LHE", local_date="2027-01-01", local_time="12:00:00+05:00"
            ),
            airport=AirportMetadata(iata_code="LHE", time_zone="Asia/Karachi"),
        )
