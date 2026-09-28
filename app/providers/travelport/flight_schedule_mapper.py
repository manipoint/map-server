"""Convert Travelport airport-local schedules into UTC instants."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.common.exceptions import ProviderUnavailableError
from app.providers.flights.metadata_schemas import AirportMetadata
from app.providers.travelport.flight_response_schemas import (
    TravelportFlightEndpoint,
)


def resolve_flight_endpoint_time(
    *,
    endpoint: TravelportFlightEndpoint,
    airport: AirportMetadata,
) -> datetime:
    """Return one unambiguous UTC instant for an airport-local schedule."""

    if endpoint.location != airport.iata_code:
        raise ProviderUnavailableError(
            "Flight airport metadata does not match the schedule"
        )

    if endpoint.local_time.tzinfo is not None:
        raise ProviderUnavailableError(
            "Expected an airport-local flight time without an offset"
        )

    local_datetime = datetime.combine(
        endpoint.local_date,
        endpoint.local_time,
    )
    zone = ZoneInfo(airport.time_zone)

    candidates: set[datetime] = set()

    for fold in (0, 1):
        zoned_datetime = local_datetime.replace(
            tzinfo=zone,
            fold=fold,
        )
        utc_datetime = zoned_datetime.astimezone(UTC)

        restored_local = utc_datetime.astimezone(zone).replace(
            tzinfo=None,
        )

        if restored_local == local_datetime:
            candidates.add(utc_datetime)

    if not candidates:
        raise ProviderUnavailableError(
            "Flight schedule contains a nonexistent local time"
        )

    if len(candidates) > 1:
        raise ProviderUnavailableError(
            "Flight schedule contains an ambiguous local time"
        )

    return next(iter(candidates))
