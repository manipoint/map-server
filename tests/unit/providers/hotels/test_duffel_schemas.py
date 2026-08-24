"""Tests for validated Duffel Stays request and response schemas."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.providers.hotels.duffel_schemas import (
    DuffelGeographicCoordinates,
    DuffelStayAccommodationResponse,
    DuffelStayGuest,
    DuffelStayLocation,
    DuffelStaySearchData,
    DuffelStaySearchPayload,
    DuffelStaySearchResponse,
)


def create_accommodation_response(
    **overrides: object,
) -> DuffelStayAccommodationResponse:
    """Create one realistic Duffel accommodation response."""

    values: dict[str, object] = {
        "id": "acc_test_123",
        "name": "Example Hotel",
        "description": "A central hotel.",
        "rating": 4,
        "review_score": "8.8",
        "review_count": 336,
        "location": {
            "address": {
                "line_one": "100 Example Street",
                "line_two": None,
                "city_name": "London",
                "region": "England",
                "postal_code": "SW1A 1AA",
                "country_code": "gb",
            },
            "geographic_coordinates": {
                "latitude": 51.5071,
                "longitude": -0.1416,
            },
        },
        "amenities": [
            {"type": "wifi", "description": "Free Wi-Fi"},
        ],
        "photos": [
            {"url": f"https://assets.example.com/hotel-{index}.jpg"}
            for index in range(4)
        ],
    }
    values.update(overrides)
    return DuffelStayAccommodationResponse.model_validate(values)


def create_search_data(**overrides: object) -> DuffelStaySearchData:
    """Create one valid Duffel Stays search body with optional overrides."""

    values: dict[str, object] = {
        "location": DuffelStayLocation(
            radius=5,
            geographic_coordinates=DuffelGeographicCoordinates(
                latitude=51.5071,
                longitude=-0.1416,
            ),
        ),
        "check_in_date": date(2026, 9, 10),
        "check_out_date": date(2026, 9, 12),
        "guests": [DuffelStayGuest(type="adult")],
        "rooms": 1,
    }
    values.update(overrides)
    return DuffelStaySearchData(**values)


def create_search_result_payload(**overrides: object) -> dict[str, object]:
    """Create one realistic Duffel hotel-search result payload."""

    values: dict[str, object] = {
        "id": "srr_test_123",
        "check_in_date": "2026-09-10",
        "check_out_date": "2026-09-12",
        "rooms": 1,
        "expires_at": "2026-09-01T12:00:00Z",
        "cheapest_rate_total_amount": "799.00",
        "cheapest_rate_currency": " gbp ",
        "accommodation": create_accommodation_response().model_dump(mode="json"),
        "provider_added_field": {"safe": "ignored"},
    }
    values.update(overrides)
    return values


def create_search_response_payload(
    *,
    results: list[dict[str, object]] | None = None,
    **data_overrides: object,
) -> dict[str, object]:
    """Create one realistic top-level Duffel Stays search response."""

    data: dict[str, object] = {
        "created_at": "2026-09-01T11:59:00Z",
        "results": [create_search_result_payload()] if results is None else results,
    }
    data.update(data_overrides)
    return {
        "data": data,
        "meta": {"request_id": "req_test_123"},
    }


def test_duffel_stay_payload_serializes_exact_provider_json() -> None:
    """The request envelope should contain only Duffel-supported fields."""

    payload = DuffelStaySearchPayload(
        data=create_search_data(
            guests=[
                DuffelStayGuest(type="adult"),
                DuffelStayGuest(type="child", age=7),
            ],
            free_cancellation_only=True,
        )
    )

    assert payload.model_dump(mode="json", exclude_none=True) == {
        "data": {
            "location": {
                "radius": 5,
                "geographic_coordinates": {
                    "latitude": 51.5071,
                    "longitude": -0.1416,
                },
            },
            "check_in_date": "2026-09-10",
            "check_out_date": "2026-09-12",
            "guests": [
                {"type": "adult"},
                {"type": "child", "age": 7},
            ],
            "rooms": 1,
            "free_cancellation_only": True,
            "mobile": True,
        }
    }


@pytest.mark.parametrize(
    "values",
    [
        {"type": "adult", "age": 17},
        {"type": "child"},
        {"type": "child", "age": -1},
        {"type": "child", "age": 18},
    ],
)
def test_duffel_stay_guest_rejects_invalid_age_classification(
    values: dict[str, object],
) -> None:
    """Adults omit ages while children require an exact supported age."""

    with pytest.raises(ValidationError):
        DuffelStayGuest.model_validate(values)


def test_duffel_stay_search_supports_single_parent_with_children() -> None:
    """One adult and multiple children may share one room."""

    search = create_search_data(
        guests=[
            DuffelStayGuest(type="adult"),
            DuffelStayGuest(type="child", age=4),
            DuffelStayGuest(type="child", age=8),
        ],
    )

    assert len(search.guests) == 3
    assert search.rooms == 1


def test_duffel_stay_search_requires_one_adult_per_room() -> None:
    """Children cannot satisfy Duffel's adult room-allocation requirement."""

    with pytest.raises(ValidationError, match="one adult per room"):
        create_search_data(
            guests=[
                DuffelStayGuest(type="adult"),
                DuffelStayGuest(type="child", age=10),
            ],
            rooms=2,
        )


def test_duffel_stay_search_does_not_apply_arbitrary_group_limit() -> None:
    """Large office groups should reach Duffel when every room has an adult."""

    search = create_search_data(
        guests=[DuffelStayGuest(type="adult") for _ in range(35)],
        rooms=20,
    )

    assert len(search.guests) == 35
    assert search.rooms == 20


def test_duffel_stay_search_accepts_exactly_ninety_nine_nights() -> None:
    """The documented maximum stay should remain valid."""

    check_in = date(2026, 9, 10)
    search = create_search_data(
        check_in_date=check_in,
        check_out_date=check_in + timedelta(days=99),
    )

    assert (search.check_out_date - search.check_in_date).days == 99


def test_duffel_stay_search_rejects_invalid_dates_and_long_stay() -> None:
    """Invalid date relationships should fail before HTTP work."""

    check_in = date(2026, 9, 10)

    with pytest.raises(ValidationError, match="checkout must be after"):
        create_search_data(
            check_in_date=check_in,
            check_out_date=check_in,
        )

    with pytest.raises(ValidationError, match="cannot exceed 99 nights"):
        create_search_data(
            check_in_date=check_in,
            check_out_date=check_in + timedelta(days=100),
        )


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(-90.1, 0.0), (90.1, 0.0), (0.0, -180.1), (0.0, 180.1)],
)
def test_duffel_coordinates_reject_out_of_range_values(
    latitude: float,
    longitude: float,
) -> None:
    """Duffel must receive valid geographic coordinate values."""

    with pytest.raises(ValidationError):
        DuffelGeographicCoordinates(
            latitude=latitude,
            longitude=longitude,
        )


@pytest.mark.parametrize("radius", [0, 101])
def test_duffel_location_rejects_unsupported_radius(radius: int) -> None:
    """Search radius should remain inside Duffel's documented range."""

    with pytest.raises(ValidationError):
        DuffelStayLocation(
            radius=radius,
            geographic_coordinates=DuffelGeographicCoordinates(
                latitude=51.5071,
                longitude=-0.1416,
            ),
        )


def test_duffel_request_models_reject_unknown_fields() -> None:
    """Unsupported provider fields should never silently enter request JSON."""

    with pytest.raises(ValidationError):
        DuffelStayGuest.model_validate(
            {
                "type": "adult",
                "unsupported": True,
            }
        )


def test_duffel_accommodation_accepts_all_provider_photos() -> None:
    """Provider parsing must not discard photos before normalization."""

    accommodation = create_accommodation_response()

    assert len(accommodation.photos) == 4
    assert accommodation.location.address.country_code == "GB"
    assert accommodation.model_dump(mode="json")["photos"][0] == {
        "url": "https://assets.example.com/hotel-0.jpg"
    }


def test_duffel_accommodation_accepts_missing_optional_collections() -> None:
    """Hotels without photos or amenity data should still be searchable."""

    without_photos = create_accommodation_response(
        photos=[],
        amenities=None,
    )
    omitted_photos = create_accommodation_response(amenities=None)
    omitted_photos = DuffelStayAccommodationResponse.model_validate(
        omitted_photos.model_dump(exclude={"photos"})
    )

    assert without_photos.photos == []
    assert without_photos.amenities is None
    assert omitted_photos.photos == []


def test_duffel_accommodation_ignores_new_response_fields() -> None:
    """Additive Duffel response changes must not break parsing."""

    accommodation = create_accommodation_response(provider_added_field={"future": True})

    assert accommodation.id == "acc_test_123"


def test_duffel_accommodation_rejects_invalid_photo_url() -> None:
    """Invalid provider photo URLs should fail typed response parsing."""

    with pytest.raises(ValidationError):
        create_accommodation_response(photos=[{"url": "not-a-url"}])


def test_duffel_search_response_parses_required_fields() -> None:
    """A valid provider response should retain exact price and expiry data."""

    response = DuffelStaySearchResponse.model_validate(create_search_response_payload())

    result = response.data.results[0]
    assert response.data.created_at.utcoffset() == timedelta(0)
    assert result.id == "srr_test_123"
    assert result.cheapest_rate_total_amount == Decimal("799.00")
    assert result.cheapest_rate_currency == "GBP"
    assert result.expires_at.utcoffset() == timedelta(0)
    assert result.accommodation.id == "acc_test_123"


def test_duffel_search_response_accepts_explicit_empty_results() -> None:
    """An explicit empty result list represents valid no availability."""

    response = DuffelStaySearchResponse.model_validate(
        create_search_response_payload(results=[])
    )

    assert response.data.results == []


def test_duffel_search_response_requires_results_field() -> None:
    """A malformed response must not silently become no availability."""

    payload = create_search_response_payload()
    del payload["data"]["results"]  # type: ignore[index]

    with pytest.raises(ValidationError):
        DuffelStaySearchResponse.model_validate(payload)


def test_duffel_search_response_accepts_more_than_ten_provider_results() -> None:
    """Provider parsing precedes mapper-level sorting and result limiting."""

    response = DuffelStaySearchResponse.model_validate(
        create_search_response_payload(
            results=[
                create_search_result_payload(id=f"srr_test_{index}")
                for index in range(11)
            ]
        )
    )

    assert len(response.data.results) == 11


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"check_out_date": "2026-09-10"},
            "checkout must be after check-in",
        ),
        (
            {"expires_at": "2026-09-01T12:00:00"},
            "expires_at must include a timezone",
        ),
    ],
)
def test_duffel_search_result_rejects_invalid_dates(
    overrides: dict[str, object],
    message: str,
) -> None:
    """Invalid stay dates and ambiguous expiry timestamps must fail parsing."""

    with pytest.raises(ValidationError, match=message):
        DuffelStaySearchResponse.model_validate(
            create_search_response_payload(
                results=[create_search_result_payload(**overrides)]
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"rooms": 0},
        {"cheapest_rate_total_amount": "-0.01"},
        {"cheapest_rate_currency": "US"},
    ],
)
def test_duffel_search_result_rejects_invalid_commercial_fields(
    overrides: dict[str, object],
) -> None:
    """Invalid room, price, and currency data must not reach normalization."""

    with pytest.raises(ValidationError):
        DuffelStaySearchResponse.model_validate(
            create_search_response_payload(
                results=[create_search_result_payload(**overrides)]
            )
        )


def test_duffel_search_response_rejects_naive_created_at() -> None:
    """The provider search timestamp must be safe for UTC comparisons."""

    with pytest.raises(
        ValidationError,
        match="created_at must include a timezone",
    ):
        DuffelStaySearchResponse.model_validate(
            create_search_response_payload(
                created_at=datetime(2026, 9, 1, 11, 59),
            )
        )
