"""Resolve metadata needed by catalog-referenced Travelport flights."""

from app.common.exceptions import ProviderUnavailableError
from app.providers.flights.metadata_provider import FlightMetadataProvider
from app.providers.flights.metadata_schemas import FlightMetadata
from app.providers.travelport.flight_response_decoder import (
    DecodedTravelportSearch,
)


async def resolve_travelport_metadata(
    *,
    decoded: DecodedTravelportSearch,
    provider: FlightMetadataProvider,
) -> FlightMetadata:
    """Resolve unique airport and carrier codes in one batch."""

    if not decoded.catalog.offerings:
        return FlightMetadata()

    product_ids = {
        reference.product_ref
        for offering in decoded.catalog.offerings
        for options in offering.product_brand_options
        for fare in options.offerings
        for reference in fare.products
    }

    flight_ids = {
        segment.flight.flight_ref
        for product_id in product_ids
        for segment in decoded.products_by_id[product_id].segments
    }

    airport_codes: set[str] = set()
    carrier_codes: set[str] = set()

    for flight_id in flight_ids:
        flight = decoded.flights_by_id[flight_id]

        airport_codes.add(flight.departure.location)
        airport_codes.add(flight.arrival.location)
        carrier_codes.add(flight.carrier)

        if flight.operating_carrier is not None:
            carrier_codes.add(flight.operating_carrier)

    metadata = await provider.resolve(
        airport_codes=frozenset(airport_codes),
        carrier_codes=frozenset(carrier_codes),
    )

    missing_airports = airport_codes.difference(metadata.airports)
    missing_airlines = carrier_codes.difference(metadata.airlines)

    if missing_airports or missing_airlines:
        raise ProviderUnavailableError("Required flight metadata is unavailable")

    return metadata
