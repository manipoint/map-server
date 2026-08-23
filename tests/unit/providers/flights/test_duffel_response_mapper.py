"""Tests for mapping parsed Duffel offers to normalized flight results."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.flights import FlightSearchStatus
from app.providers.flights.duffel_mapper import (
    duration_minutes,
    map_duffel_offer,
    map_duffel_search_result,
)
from app.providers.flights.duffel_schemas import (
    DuffelOfferResponse,
    DuffelOffersListResponse,
)
from app.providers.flights.schemas import FlightSearchInput


def create_search(**overrides: object) -> FlightSearchInput:
    """Create a normalized search used by response-mapping tests."""

    values: dict[str, object] = {
        "origin": "LHE",
        "destination": "DXB",
        "departure_date": date(2026, 9, 10),
        "adults": 2,
    }
    values.update(overrides)
    return FlightSearchInput(**values)


def create_segment_payload(**overrides: object) -> dict[str, object]:
    """Create a parsed-response-compatible Duffel segment payload."""

    values: dict[str, object] = {
        "origin": {"iata_code": "LHE"},
        "destination": {"iata_code": "DXB"},
        "departing_at": "2026-09-10T08:00:00",
        "arriving_at": "2026-09-10T11:00:00",
        "duration": "PT3H",
        "marketing_carrier": {"name": "Example Air", "iata_code": "EX"},
        "marketing_carrier_flight_number": "101",
        "operating_carrier": {"name": "Partner Air", "iata_code": "PA"},
        "operating_carrier_flight_number": "501",
    }
    values.update(overrides)
    return values


def create_offer_payload(**overrides: object) -> dict[str, object]:
    """Create a complete minimal Duffel offer payload."""

    values: dict[str, object] = {
        "id": "off_1",
        "total_amount": "725.50",
        "total_currency": "USD",
        "expires_at": "2026-09-10T07:30:00Z",
        "slices": [
            {
                "duration": "PT3H",
                "segments": [create_segment_payload()],
            }
        ],
        "passengers": [{"id": "pas_1"}, {"id": "pas_2"}],
    }
    values.update(overrides)
    return values


def create_offer(**overrides: object) -> DuffelOfferResponse:
    """Parse a customizable raw offer through the Duffel response schema."""

    return DuffelOfferResponse.model_validate(create_offer_payload(**overrides))


def test_duration_rounds_partial_minutes_up() -> None:
    """Normalized durations must not understate provider travel time."""

    assert duration_minutes(timedelta(seconds=61)) == 2


def test_offer_mapper_preserves_codeshare_price_currency_and_expiry() -> None:
    """One Duffel offer should retain its customer-visible critical fields."""

    raw_offer = create_offer()

    offer = map_duffel_offer(
        raw_offer,
        expected_travelers=2,
        expected_slices=1,
    )

    segment = offer.outbound.segments[0]
    assert offer.offer_id == "off_1"
    assert offer.total_price == Decimal("725.50")
    assert offer.currency == "USD"
    assert offer.expires_at == datetime(2026, 9, 10, 7, 30, tzinfo=UTC)
    assert offer.traveler_count == 2
    assert offer.refundable is None
    assert offer.seats_available is None
    assert segment.marketing_carrier_code == "EX"
    assert segment.marketing_carrier_name == "Example Air"
    assert segment.marketing_flight_number == "101"
    assert segment.operating_carrier_code == "PA"
    assert segment.operating_carrier_name == "Partner Air"
    assert segment.operating_flight_number == "501"


def test_offer_mapper_computes_connection_and_return_itineraries() -> None:
    """Multiple segments and the second slice should remain distinct."""

    connection = create_segment_payload(
        origin={"iata_code": "DXB"},
        destination={"iata_code": "LHR"},
        departing_at="2026-09-10T13:00:00",
        arriving_at="2026-09-10T17:00:00",
        duration="PT4H",
    )
    inbound = create_segment_payload(
        origin={"iata_code": "LHR"},
        destination={"iata_code": "LHE"},
        departing_at="2026-09-15T09:00:00",
        arriving_at="2026-09-15T17:00:00",
        duration="PT8H",
    )
    raw_offer = create_offer(
        slices=[
            {
                "duration": "PT9H",
                "segments": [create_segment_payload(), connection],
            },
            {"duration": "PT8H", "segments": [inbound]},
        ]
    )

    offer = map_duffel_offer(
        raw_offer,
        expected_travelers=2,
        expected_slices=2,
    )

    assert offer.outbound.stops == 1
    assert offer.outbound.duration_minutes == 540
    assert offer.return_itinerary is not None
    assert offer.return_itinerary.stops == 0
    assert offer.return_itinerary.duration_minutes == 480


def test_offer_mapper_rejects_provider_passenger_mismatch() -> None:
    """A quote for a different party must never be shown to the user."""

    with pytest.raises(ValueError, match="passenger count does not match"):
        map_duffel_offer(
            create_offer(),
            expected_travelers=3,
            expected_slices=1,
        )


def test_offer_mapper_rejects_provider_slice_mismatch() -> None:
    """A one-way response must not satisfy a round-trip search."""

    with pytest.raises(ValueError, match="slice count does not match"):
        map_duffel_offer(
            create_offer(),
            expected_travelers=2,
            expected_slices=2,
        )


def test_search_mapper_filters_expired_offers_and_honors_result_limit() -> None:
    """Only current offers inside the requested cost boundary should be returned."""

    searched_at = datetime(2026, 9, 10, 7, tzinfo=UTC)
    response = DuffelOffersListResponse(
        data=[
            create_offer(id="off_expired", expires_at=searched_at),
            create_offer(id="off_1", expires_at=searched_at + timedelta(minutes=10)),
            create_offer(id="off_2", expires_at=searched_at + timedelta(minutes=20)),
            create_offer(id="off_3", expires_at=searched_at + timedelta(minutes=30)),
        ]
    )

    result = map_duffel_search_result(
        response=response,
        request=create_search(max_results=2, currency="PKR"),
        searched_at=searched_at,
    )

    assert result.status is FlightSearchStatus.OFFERS_AVAILABLE
    assert [offer.offer_id for offer in result.offers] == ["off_1", "off_2"]
    assert [offer.currency for offer in result.offers] == ["USD", "USD"]


def test_search_mapper_returns_no_offers_when_every_offer_is_expired() -> None:
    """Stale provider data should become a safe no-availability result."""

    searched_at = datetime(2026, 9, 10, 7, tzinfo=UTC)
    response = DuffelOffersListResponse(data=[create_offer(expires_at=searched_at)])

    result = map_duffel_search_result(
        response=response,
        request=create_search(),
        searched_at=searched_at,
    )

    assert result.status is FlightSearchStatus.NO_OFFERS
    assert result.offers == []


def test_search_mapper_rejects_naive_search_time() -> None:
    """Expiry comparison requires an absolute search timestamp."""

    with pytest.raises(ValueError, match="searched_at must include a timezone"):
        map_duffel_search_result(
            response=DuffelOffersListResponse(data=[]),
            request=create_search(),
            searched_at=datetime(2026, 9, 10, 7),
        )
