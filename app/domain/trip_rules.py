"""Pure business rules shared by trip input and persistence boundaries."""

from collections.abc import Iterable
from datetime import date, timedelta

MAX_TRAVELERS_PER_REQUEST = 100


def validate_trip_dates(start_date: date | None, end_date: date | None) -> None:
    """Trips currently require at least two calendar days."""
    if start_date is not None and end_date is not None and end_date <= start_date:
        raise ValueError("end_date must be after start_date")


def inclusive_day_count(start_date: date, end_date: date) -> int:
    """Count calendar days; individual contracts validate permitted ranges."""
    return (end_date - start_date).days + 1


def end_date_from_duration(start_date: date, duration_days: int) -> date:
    """Resolve inclusive duration without allowing calendar overflow."""
    if duration_days < 1:
        raise ValueError("duration_days must be positive")
    try:
        return start_date + timedelta(days=duration_days - 1)
    except OverflowError:
        raise ValueError("duration_days exceeds the supported calendar range") from None


def validate_flight_dates(departure_date: date, return_date: date | None) -> None:
    """Flights, unlike multi-day trips, allow same-day returns."""
    if return_date is not None and return_date < departure_date:
        raise ValueError("return_date must be on or after departure_date")


def validate_distinct_locations(origin: str | None, destination: str | None) -> None:
    if (
        origin is not None
        and destination is not None
        and origin.casefold() == destination.casefold()
    ):
        raise ValueError("origin and destination must be different")


def validate_lap_infants(adults: int | None, lap_infants: int) -> None:
    if adults is not None and lap_infants > adults:
        raise ValueError(
            "each lap infant must be accompanied by one adult; "
            "book additional infants with their own seat"
        )


def validate_room_allocation(adults: int | None, rooms: int | None) -> None:
    if adults is not None and rooms is not None and rooms > adults:
        raise ValueError("each room requires at least one adult")


def validate_traveler_count(total: int) -> None:
    if total > MAX_TRAVELERS_PER_REQUEST:
        raise ValueError(f"traveler count cannot exceed {MAX_TRAVELERS_PER_REQUEST}")


def normalize_interests(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        interest = value.strip()
        key = interest.casefold()
        if key not in seen:
            normalized.append(interest)
            seen.add(key)
    return normalized
