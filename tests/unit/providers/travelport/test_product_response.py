"""Product and passenger fare reference validation."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.providers.travelport.flight_response_schemas import (
    TravelportFlightProductResponse,
    TravelportFlightResponse,
    TravelportProductResponse,
)


def product_payload():
    return {
        "@type": "ProductAir",
        "id": "p1",
        "totalDuration": "PT9H",
        "FlightSegment": [
            {"sequence": 1, "Flight": {"FlightRef": "f1"}},
            {"sequence": 2, "Flight": {"FlightRef": "f2"}},
        ],
        "PassengerFlight": [
            {
                "passengerQuantity": 1,
                "passengerTypeCode": "ADT",
                "FlightProduct": [
                    {"segmentSequence": [1], "cabin": "Economy", "classOfService": "B"},
                    {"segmentSequence": [2], "cabin": "Business"},
                ],
            }
        ],
    }


def test_mixed_cabins_and_references_survive_round_trip():
    product = TravelportProductResponse.model_validate(product_payload())
    assert product.total_duration == timedelta(hours=9)
    assert [s.flight.flight_ref for s in product.segments] == ["f1", "f2"]
    assert [f.cabin for f in product.passenger_flights[0].flight_products] == [
        "Economy",
        "Business",
    ]
    assert (
        TravelportProductResponse.model_validate_json(
            product.model_dump_json(by_alias=True)
        )
        == product
    )


def test_one_fare_can_cover_multiple_segments_and_passenger_categories():
    payload = product_payload()
    fare = {"segmentSequence": [1, 2], "cabin": "PremiumEconomy"}
    payload["PassengerFlight"] = [
        {"passengerQuantity": 2, "passengerTypeCode": "ADT", "FlightProduct": [fare]},
        {"passengerQuantity": 1, "passengerTypeCode": "CNN", "FlightProduct": [fare]},
    ]
    assert len(TravelportProductResponse.model_validate(payload).passenger_flights) == 2


@pytest.mark.parametrize(
    ("references", "message"),
    [
        ([[1], [3]], "unknown segment"),
        ([[1], [1, 2]], "more than once"),
        ([[1]], "every product segment"),
    ],
)
def test_invalid_fare_segment_coverage_is_rejected(references, message):
    payload = product_payload()
    payload["PassengerFlight"][0]["FlightProduct"] = [
        {"segmentSequence": sequences, "cabin": "Economy"} for sequences in references
    ]
    with pytest.raises(ValidationError, match=message):
        TravelportProductResponse.model_validate(payload)


def test_duplicate_product_sequences_are_rejected():
    payload = product_payload()
    payload["FlightSegment"][1]["sequence"] = 1
    with pytest.raises(ValidationError, match="must be unique"):
        TravelportProductResponse.model_validate(payload)


@pytest.mark.parametrize("sequences", [[], [0], [-1], [1, 1]])
def test_invalid_fare_sequences_are_rejected(sequences):
    with pytest.raises(ValidationError):
        TravelportFlightProductResponse(segment_sequences=sequences, cabin="Economy")


def test_unknown_cabin_is_preserved_for_explicit_downstream_handling():
    result = TravelportFlightProductResponse(segment_sequences=[1], cabin="FutureCabin")
    assert result.cabin == "FutureCabin"


@pytest.mark.parametrize("field", ["FlightSegment", "PassengerFlight"])
def test_empty_product_collections_are_rejected(field):
    payload = product_payload()
    payload[field] = []
    with pytest.raises(ValidationError):
        TravelportProductResponse.model_validate(payload)


@pytest.mark.parametrize("duration", ["PT0S", "-PT1H", "invalid"])
def test_invalid_journey_duration_is_rejected(duration):
    payload = product_payload()
    payload["totalDuration"] = duration
    with pytest.raises(ValidationError):
        TravelportProductResponse.model_validate(payload)


def test_flight_keeps_local_times_and_provider_duration_independent():
    flight = TravelportFlightResponse.model_validate(
        {
            "id": "f1",
            "carrier": "AA",
            "number": "171",
            "stops": 0,
            "duration": "PT6H11M",
            "Departure": {"location": "JFK", "date": "2027-11-07", "time": "06:30:00"},
            "Arrival": {"location": "LAX", "date": "2027-11-07", "time": "09:41:00"},
        }
    )
    assert flight.duration == timedelta(hours=6, minutes=11)
    assert flight.departure.local_time.tzinfo is None
    assert flight.arrival.local_time.tzinfo is None
