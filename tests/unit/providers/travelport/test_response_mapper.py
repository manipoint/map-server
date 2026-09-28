"""Normalized flight and itinerary mapping regression tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.providers.flights.metadata_schemas import FlightMetadata
from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.flight_response_decoder import DecodedTravelportSearch
from app.providers.travelport.flight_response_mapper import (
    duration_in_minutes,
    map_travelport_flight,
    map_travelport_itinerary,
    map_travelport_one_way_result,
)
from app.providers.travelport.flight_response_schemas import (
    TravelportCatalogProductOfferings,
    TravelportFlightResponse,
    TravelportProductResponse,
    TravelportResult,
)


def metadata():
    return FlightMetadata(
        airports={
            c: {"iata_code": c, "time_zone": "Asia/Karachi"}
            for c in ("LHE", "KHI", "ISB")
        },
        airlines={
            "PK": {"carrier_code": "PK", "name": "Marketing Airline"},
            "AA": {"carrier_code": "AA", "name": "Operating Airline"},
        },
    )


def flight(*, second=False, **overrides):
    data = {
        "id": "f2" if second else "f1",
        "carrier": "PK",
        "number": "123",
        "duration": "PT2H",
        "stops": 0,
        "Departure": {
            "location": "KHI" if second else "LHE",
            "date": "2027-11-07",
            "time": "13:00:00" if second else "10:00:00",
        },
        "Arrival": {
            "location": "ISB" if second else "KHI",
            "date": "2027-11-07",
            "time": "15:00:00" if second else "12:00:00",
        },
    }
    data.update(overrides)
    return TravelportFlightResponse.model_validate(data)


def product(duration="PT5H"):
    return TravelportProductResponse.model_validate(
        {
            "id": "p1",
            "totalDuration": duration,
            "FlightSegment": [
                {"sequence": 2, "Flight": {"FlightRef": "f2"}},
                {"sequence": 1, "Flight": {"FlightRef": "f1"}},
            ],
            "PassengerFlight": [
                {
                    "passengerQuantity": 1,
                    "passengerTypeCode": "ADT",
                    "FlightProduct": [{"segmentSequence": [1, 2], "cabin": "Economy"}],
                }
            ],
        }
    )


def test_segment_preserves_utc_timezone_stops_and_unknown_operator():
    result = map_travelport_flight(flight=flight(stops=1), metadata=metadata())
    assert result.departure_at == datetime(2027, 11, 7, 5, tzinfo=UTC)
    assert result.arrival_at == datetime(2027, 11, 7, 7, tzinfo=UTC)
    assert result.departure_time_zone == "Asia/Karachi"
    assert result.duration_minutes == 120
    assert result.intermediate_stops == 1
    assert result.operating_carrier_code is None
    assert result.operating_carrier_name is None
    assert result.operating_flight_number is None


@pytest.mark.parametrize("provider_name", [None, "Provider Operator Name"])
def test_codeshare_preserves_operating_identity(provider_name):
    result = map_travelport_flight(
        flight=flight(
            operatingCarrier="AA",
            operatingCarrierNumber="456",
            operatingCarrierName=provider_name,
        ),
        metadata=metadata(),
    )
    assert result.marketing_carrier_code == "PK"
    assert result.operating_carrier_code == "AA"
    assert result.operating_flight_number == "456"
    assert result.operating_carrier_name == (provider_name or "Operating Airline")


@pytest.mark.parametrize(
    "duration",
    [
        timedelta(0),
        timedelta(seconds=-60),
        timedelta(seconds=30),
        timedelta(seconds=61),
    ],
)
def test_duration_does_not_truncate_or_accept_nonpositive_values(duration):
    with pytest.raises(ProviderUnavailableError):
        duration_in_minutes(duration)


def test_positive_whole_minute_duration():
    assert duration_in_minutes(timedelta(days=1, minutes=15)) == 1455


def test_missing_metadata_is_sanitized():
    with pytest.raises(
        ProviderUnavailableError, match="metadata is unavailable"
    ) as caught:
        map_travelport_flight(flight=flight(), metadata=FlightMetadata())
    assert caught.value.__suppress_context__


def test_itinerary_orders_segments_includes_layover_and_counts_all_stops():
    source = product()
    result = map_travelport_itinerary(
        product=source,
        flights_by_id={"f1": flight(stops=1), "f2": flight(second=True)},
        metadata=metadata(),
    )
    assert [s.departure_airport for s in result.segments] == ["LHE", "KHI"]
    assert result.duration_minutes == 300
    assert result.stops == 2
    assert [s.sequence for s in source.segments] == [2, 1]


def test_missing_flight_reference_is_rejected():
    with pytest.raises(ProviderUnavailableError, match="unavailable flight"):
        map_travelport_itinerary(
            product=product(), flights_by_id={"f1": flight()}, metadata=metadata()
        )


@pytest.mark.parametrize(
    ("origin", "departure", "message"),
    [
        ("LHE", "13:00:00", "airport transfer"),
        ("KHI", "11:00:00", "overlapping"),
        ("KHI", "12:00:00", "overlapping"),
    ],
)
def test_invalid_connections_are_rejected(origin, departure, message):
    second = flight(
        second=True,
        Departure={"location": origin, "date": "2027-11-07", "time": departure},
    )
    with pytest.raises(ProviderUnavailableError, match=message):
        map_travelport_itinerary(
            product=product(),
            flights_by_id={"f1": flight(), "f2": second},
            metadata=metadata(),
        )


def test_total_duration_cannot_omit_layover():
    # Two two-hour flights with a one-hour connection require five hours.
    with pytest.raises(ProviderUnavailableError):
        map_travelport_itinerary(
            product=product("PT4H"),
            flights_by_id={"f1": flight(), "f2": flight(second=True)},
            metadata=metadata(),
        )


def decoded_offers(prices=("300", "100", "200"), currencies=None):
    fares = [
        {
            "Product": [{"productRef": "p1"}],
            "ContentSource": "NDC",
            "BestCombinablePrice": {
                "CurrencyCode": {"value": currencies[i] if currencies else "USD"},
                "TotalPrice": price,
            },
        }
        for i, price in enumerate(prices)
    ]
    catalog = TravelportCatalogProductOfferings.model_validate(
        {
            "CatalogProductOffering": [
                {
                    "id": "o1",
                    "sequence": 1,
                    "Departure": "LHE",
                    "Arrival": "ISB",
                    "ProductBrandOptions": [{"ProductBrandOffering": fares}],
                }
            ]
            if fares
            else []
        }
    )
    return DecodedTravelportSearch(
        catalog=catalog,
        products_by_id={"p1": product()},
        flights_by_id={"f1": flight(), "f2": flight(second=True)},
        result=TravelportResult(),
    )


def map_result(
    decoded=None,
    search_id=None,
    searched_at=datetime(2027, 1, 1, tzinfo=UTC),
    **overrides,
):
    values = {"origin": "LHE", "destination": "ISB", "departure_date": "2027-11-07"}
    values.update(overrides)
    return map_travelport_one_way_result(
        request=FlightSearchInput(**values),
        decoded=decoded or decoded_offers(),
        metadata=metadata(),
        search_id=search_id if search_id is not None else UUID(int=1),
        searched_at=searched_at,
    )


def test_result_sorts_before_applying_limit_and_preserves_unknowns():
    result = map_result(max_results=2)
    assert result.status.value == "offers_available"
    assert [o.total_price for o in result.offers] == [Decimal("100"), Decimal("200")]
    assert all(
        o.return_itinerary is None
        and o.expires_at is None
        and o.refundable is None
        and o.seats_available is None
        for o in result.offers
    )
    assert all(o.traveler_count == 1 for o in result.offers)


def test_result_ids_are_unique_stable_and_scoped_to_search():
    first = map_result(decoded_offers(("100", "100")))
    second = map_result(decoded_offers(("100", "100")))
    other = map_result(decoded_offers(("100", "100")), search_id=UUID(int=2))
    ids = [o.offer_id for o in first.offers]
    assert len(set(ids)) == 2
    assert ids == [o.offer_id for o in second.offers]
    assert set(ids).isdisjoint(o.offer_id for o in other.offers)


@pytest.mark.parametrize(
    "overrides",
    [
        {"destination": "KHI"},
        {"departure_date": "2027-11-08"},
        {"nonstop_only": True},
        {"cabin_class": "business"},
    ],
)
def test_nonmatching_preferences_produce_no_offers(overrides):
    result = map_result(**overrides)
    assert result.status.value == "no_offers"
    assert result.offers == []


def test_empty_catalog_produces_no_offers():
    assert map_result(decoded_offers(())).status.value == "no_offers"


def test_return_search_is_explicitly_rejected_even_with_empty_catalog():
    with pytest.raises(ProviderUnavailableError, match="return search"):
        map_result(decoded_offers(()), return_date="2027-11-10")


def test_actual_currency_is_preserved_and_mixed_currencies_rejected():
    assert map_result(decoded_offers(("100",), ("INR",))).offers[0].currency == "INR"
    with pytest.raises(ProviderUnavailableError, match="inconsistent currencies"):
        map_result(decoded_offers(("100", "200"), ("USD", "INR")), max_results=1)


def test_invalid_traveler_count_is_not_silently_no_offers():
    with pytest.raises(ProviderUnavailableError, match="traveler count"):
        map_result(adults=2)


def test_missing_product_is_not_silently_no_offers():
    decoded = decoded_offers()
    decoded.products_by_id.clear()
    with pytest.raises(ProviderUnavailableError, match="unavailable product"):
        map_result(decoded)


def test_naive_search_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        map_result(searched_at=datetime(2027, 1, 1))
