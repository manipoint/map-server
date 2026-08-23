"""Tests for minimal forward-compatible Duffel response schemas."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.providers.flights.duffel_schemas import (
    DuffelOfferRequestResponse,
    DuffelOffersListResponse,
)


def create_segment_payload(**overrides: object) -> dict[str, object]:
    """Create one realistic Duffel segment response payload."""

    values: dict[str, object] = {
        "origin": {"iata_code": " lhe ", "name": "Lahore"},
        "destination": {"iata_code": " dxb ", "name": "Dubai"},
        "departing_at": "2026-09-10T08:00:00",
        "arriving_at": "2026-09-10T10:00:00",
        "duration": "PT2H",
        "marketing_carrier": {
            "name": " Example Airways ",
            "iata_code": " ex ",
        },
        "marketing_carrier_flight_number": " ex101 ",
        "operating_carrier": {
            "name": " Partner Airways ",
            "iata_code": " pa ",
        },
        "operating_carrier_flight_number": " pa501 ",
        "new_provider_field": {"safe": "ignored"},
    }
    values.update(overrides)
    return values


def create_offer_payload(**overrides: object) -> dict[str, object]:
    """Create one realistic Duffel list-offers item."""

    values: dict[str, object] = {
        "id": "off_123",
        "total_amount": "725.50",
        "total_currency": " usd ",
        "expires_at": "2026-09-10T07:30:00Z",
        "slices": [
            {
                "duration": "PT2H",
                "segments": [create_segment_payload()],
                "fare_brand_name": "Basic",
            }
        ],
        "passengers": [
            {"id": "pas_1", "type": "adult"},
            {"id": "pas_2", "age": 8},
        ],
        "owner": {"name": "Example Airways"},
    }
    values.update(overrides)
    return values


def test_offer_request_response_keeps_reference_and_ignores_new_fields() -> None:
    """Create-request parsing should need only its stable identifier."""

    response = DuffelOfferRequestResponse.model_validate(
        {
            "data": {
                "id": "orq_123",
                "live_mode": False,
                "future_field": "ignored",
            },
            "meta": {"request_id": "req_123"},
        }
    )

    assert response.data.id == "orq_123"


def test_list_response_parses_normalized_codes_price_duration_and_expiry() -> None:
    """Minimal Duffel fields should parse without retaining its large payload."""

    response = DuffelOffersListResponse.model_validate(
        {"data": [create_offer_payload()], "meta": {"limit": 5}}
    )

    offer = response.data[0]
    flight_slice = offer.slices[0]
    segment = flight_slice.segments[0]
    assert offer.total_amount == Decimal("725.50")
    assert offer.total_currency == "USD"
    assert offer.expires_at.utcoffset() == timedelta(0)
    assert flight_slice.duration == timedelta(hours=2)
    assert segment.duration == timedelta(hours=2)
    assert segment.origin.iata_code == "LHE"
    assert segment.destination.iata_code == "DXB"
    assert segment.marketing_carrier.name == "Example Airways"
    assert segment.marketing_carrier.iata_code == "EX"
    assert segment.marketing_carrier_flight_number == "EX101"
    assert segment.operating_carrier.name == "Partner Airways"
    assert segment.operating_carrier.iata_code == "PA"
    assert segment.operating_carrier_flight_number == "PA501"
    assert segment.departing_at.utcoffset() is None


def test_list_response_accepts_no_available_offers() -> None:
    """An empty Duffel data list is a valid no-availability result."""

    response = DuffelOffersListResponse.model_validate({"data": []})

    assert response.data == []


def test_duffel_offer_rejects_naive_expiry() -> None:
    """Offer expiry must remain safe for later stale-offer filtering."""

    with pytest.raises(ValidationError, match="expires_at must include a timezone"):
        DuffelOffersListResponse.model_validate(
            {"data": [create_offer_payload(expires_at="2026-09-10T07:30:00")]}
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration": "PT0S"},
        {"origin": {"iata_code": "12A"}},
        {"marketing_carrier": {"name": "Example", "iata_code": "E@"}},
    ],
)
def test_duffel_segment_rejects_malformed_required_fields(
    overrides: dict[str, object],
) -> None:
    """Invalid provider data should fail before normalized mapping."""

    with pytest.raises(ValidationError):
        DuffelOffersListResponse.model_validate(
            {
                "data": [
                    create_offer_payload(
                        slices=[
                            {
                                "duration": "PT2H",
                                "segments": [create_segment_payload(**overrides)],
                            }
                        ]
                    )
                ]
            }
        )


def test_duffel_offer_list_rejects_more_than_ten_items() -> None:
    """Unexpected provider payload size must respect the LLM cost boundary."""

    with pytest.raises(ValidationError):
        DuffelOffersListResponse.model_validate(
            {"data": [create_offer_payload(id=f"off_{index}") for index in range(11)]}
        )
