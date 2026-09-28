"""Batch metadata lookup follows catalog references without live providers."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.providers.flights.metadata_provider import FlightMetadataProvider
from app.providers.flights.metadata_schemas import FlightMetadata
from app.providers.travelport.flight_metadata_resolver import (
    resolve_travelport_metadata,
)
from app.providers.travelport.flight_response_decoder import decode_travelport_search


def decoded_search(*, empty=False):
    def flight(identifier, origin, destination, carrier):
        return {
            "id": identifier,
            "carrier": carrier,
            "number": "123",
            "duration": "PT2H",
            "stops": 0,
            "Departure": {"location": origin, "date": "2027-11-07", "time": "10:00:00"},
            "Arrival": {
                "location": destination,
                "date": "2027-11-07",
                "time": "12:00:00",
            },
        }

    def product(identifier, flight_ids):
        return {
            "id": identifier,
            "totalDuration": "PT5H",
            "FlightSegment": [
                {"sequence": i, "Flight": {"FlightRef": f}}
                for i, f in enumerate(flight_ids, 1)
            ],
            "PassengerFlight": [
                {
                    "passengerQuantity": 1,
                    "passengerTypeCode": "ADT",
                    "FlightProduct": [
                        {
                            "segmentSequence": list(range(1, len(flight_ids) + 1)),
                            "cabin": "Economy",
                        }
                    ],
                }
            ],
        }

    fare = {
        "Product": [{"productRef": "p1"}],
        "ContentSource": "NDC",
        "BestCombinablePrice": {"CurrencyCode": {"value": "USD"}, "TotalPrice": "100"},
    }
    offerings = [
        {
            "id": "o1",
            "sequence": 1,
            "Departure": "JFK",
            "Arrival": "SFO",
            "ProductBrandOptions": [{"ProductBrandOffering": [fare, fare]}],
        }
    ]
    return decode_travelport_search(
        {
            "CatalogProductOfferingsResponse": {
                "CatalogProductOfferings": {
                    "CatalogProductOffering": [] if empty else offerings
                },
                "ReferenceList": [
                    {
                        "@type": "ReferenceListFlight",
                        "Flight": [
                            flight("f1", "JFK", "LAX", "AA"),
                            flight("f2", "LAX", "SFO", "AA"),
                            flight("unused", "LHE", "KHI", "PK"),
                        ],
                    },
                    {
                        "@type": "ReferenceListProduct",
                        "Product": [
                            product("p1", ["f1", "f2"]),
                            product("unused", ["unused"]),
                        ],
                    },
                ],
            }
        }
    )


def metadata():
    return FlightMetadata(
        airports={
            code: {"iata_code": code, "time_zone": zone}
            for code, zone in {
                "JFK": "America/New_York",
                "LAX": "America/Los_Angeles",
                "SFO": "America/Los_Angeles",
            }.items()
        },
        airlines={"AA": {"carrier_code": "AA", "name": "American Airlines"}},
    )


def test_one_batch_deduplicates_codes_and_ignores_unused_references():
    provider = AsyncMock(spec=FlightMetadataProvider)
    provider.resolve.return_value = expected = metadata()
    decoded = decoded_search()
    original_product_ids = set(decoded.products_by_id)
    result = asyncio.run(
        resolve_travelport_metadata(decoded=decoded, provider=provider)
    )
    assert result is expected
    assert set(decoded.products_by_id) == original_product_ids
    provider.resolve.assert_awaited_once_with(
        airport_codes=frozenset({"JFK", "LAX", "SFO"}),
        carrier_codes=frozenset({"AA"}),
    )


def test_empty_catalog_skips_lookup_even_when_reference_lists_are_present():
    provider = AsyncMock(spec=FlightMetadataProvider)
    result = asyncio.run(
        resolve_travelport_metadata(
            decoded=decoded_search(empty=True), provider=provider
        )
    )
    assert result == FlightMetadata()
    provider.resolve.assert_not_awaited()


@pytest.mark.parametrize("missing", ["airport", "airline", "both"])
def test_incomplete_metadata_fails_without_fabricating_values(missing):
    provider = AsyncMock(spec=FlightMetadataProvider)
    result = metadata()
    if missing in {"airport", "both"}:
        del result.airports["LAX"]
    if missing in {"airline", "both"}:
        result.airlines.clear()
    provider.resolve.return_value = result
    with pytest.raises(
        ProviderUnavailableError, match="Required flight metadata is unavailable"
    ):
        asyncio.run(
            resolve_travelport_metadata(decoded=decoded_search(), provider=provider)
        )
    assert provider.resolve.await_count == 1


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailableError("Metadata unavailable"),
        TimeoutError(),
        asyncio.CancelledError(),
    ],
)
def test_provider_failures_and_cancellation_propagate_without_retry(error):
    provider = AsyncMock(spec=FlightMetadataProvider)
    provider.resolve.side_effect = error
    with pytest.raises(type(error)) as caught:
        asyncio.run(
            resolve_travelport_metadata(decoded=decoded_search(), provider=provider)
        )
    assert caught.value is error
    assert provider.resolve.await_count == 1
