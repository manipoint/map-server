"""Tests for mapping normalized searches to and from Duffel Stays."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.hotels import HotelSearchStatus
from app.providers.hotels.duffel_mapper import (
    build_duffel_stay_search,
    map_duffel_accommodation,
    map_duffel_stay_result,
    map_duffel_stay_search_response,
)
from app.providers.hotels.duffel_schemas import (
    DuffelStayAccommodationResponse,
    DuffelStaySearchResponse,
    DuffelStaySearchResultResponse,
)
from app.providers.hotels.schemas import (
    HotelSearchInput,
    ResolvedHotelSearch,
)
from app.providers.locations.schemas import ResolvedLocation


def create_resolved_search(**request_overrides: object) -> ResolvedHotelSearch:
    """Create one provider-ready hotel search with optional request overrides."""

    request_values: dict[str, object] = {
        "destination": "London",
        "check_in_date": date(2026, 9, 10),
        "check_out_date": date(2026, 9, 12),
    }
    request_values.update(request_overrides)
    return ResolvedHotelSearch(
        request=HotelSearchInput(**request_values),
        location=ResolvedLocation(
            query="London",
            display_name="London, United Kingdom",
            latitude=51.5071,
            longitude=-0.1416,
        ),
        radius_km=5,
    )


def create_accommodation_response(
    **overrides: object,
) -> DuffelStayAccommodationResponse:
    """Create one Duffel accommodation for response-mapping tests."""

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
                "line_two": "Westminster",
                "city_name": "London",
                "region": "England",
                "postal_code": "SW1A 1AA",
                "country_code": "GB",
            },
            "geographic_coordinates": {
                "latitude": 51.5071,
                "longitude": -0.1416,
            },
        },
        "amenities": [
            {"type": "wifi", "description": "Free Wi-Fi"},
            {"type": "internet", "description": "free wi-fi"},
            {"type": "pool"},
        ],
        "photos": [
            {"url": "https://assets.example.com/hotel-1.jpg"},
            {"url": "https://assets.example.com/hotel-1.jpg"},
            {"url": "https://assets.example.com/hotel-2.jpg"},
            {"url": "https://assets.example.com/hotel-3.jpg"},
            {"url": "https://assets.example.com/hotel-4.jpg"},
        ],
    }
    values.update(overrides)
    return DuffelStayAccommodationResponse.model_validate(values)


def create_search_result_response(
    **overrides: object,
) -> DuffelStaySearchResultResponse:
    """Create one current Duffel hotel-search result."""

    values: dict[str, object] = {
        "id": "srr_test_123",
        "check_in_date": "2026-09-10",
        "check_out_date": "2026-09-12",
        "rooms": 1,
        "expires_at": "2026-09-01T12:00:00Z",
        "cheapest_rate_total_amount": "799.00",
        "cheapest_rate_currency": "GBP",
        "accommodation": create_accommodation_response(),
    }
    values.update(overrides)
    return DuffelStaySearchResultResponse.model_validate(values)


def create_search_response(
    results: list[DuffelStaySearchResultResponse],
) -> DuffelStaySearchResponse:
    """Wrap Duffel results in the provider response envelope."""

    return DuffelStaySearchResponse.model_validate(
        {
            "data": {
                "created_at": "2026-08-24T12:00:00Z",
                "results": results,
            }
        }
    )


def test_mapper_builds_exact_minimal_duffel_stays_payload() -> None:
    """A basic one-room search should contain only supported provider fields."""

    payload = build_duffel_stay_search(create_resolved_search())

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
            "guests": [{"type": "adult"}],
            "rooms": 1,
            "free_cancellation_only": False,
            "mobile": True,
        }
    }


def test_mapper_preserves_single_parent_and_child_ages() -> None:
    """Children must never disappear or lose ages during provider mapping."""

    payload = build_duffel_stay_search(
        create_resolved_search(
            adults=1,
            children_ages=[0, 8, 17],
        )
    )

    assert [guest.model_dump(exclude_none=True) for guest in payload.data.guests] == [
        {"type": "adult"},
        {"type": "child", "age": 0},
        {"type": "child", "age": 8},
        {"type": "child", "age": 17},
    ]


def test_mapper_preserves_large_group_and_multiple_rooms() -> None:
    """Large valid groups should reach Duffel without arbitrary truncation."""

    search = create_resolved_search(
        adults=35,
        children_ages=[5, 10, 15],
        rooms=20,
    )

    payload = build_duffel_stay_search(search)

    assert len(payload.data.guests) == search.request.total_guests
    assert sum(guest.type == "adult" for guest in payload.data.guests) == 35
    assert payload.data.rooms == 20


def test_mapper_preserves_radius_coordinates_and_cancellation_filter() -> None:
    """Resolved location and availability filters should reach Duffel unchanged."""

    search = ResolvedHotelSearch(
        request=HotelSearchInput(
            destination="Henderson Island",
            check_in_date=date(2026, 9, 10),
            check_out_date=date(2026, 9, 12),
            free_cancellation_only=True,
        ),
        location=ResolvedLocation(
            query="Henderson Island",
            display_name="Henderson Island, Pitcairn Islands",
            latitude=-24.38,
            longitude=-128.32,
        ),
        radius_km=2,
    )

    payload = build_duffel_stay_search(search)

    assert payload.data.location.radius == 2
    assert payload.data.location.geographic_coordinates.latitude == -24.38
    assert payload.data.location.geographic_coordinates.longitude == -128.32
    assert payload.data.free_cancellation_only is True


def test_mapper_excludes_model_only_search_preferences() -> None:
    """Destination text and result limit must not enter Duffel's request body."""

    payload = build_duffel_stay_search(
        create_resolved_search(
            destination="Central London",
            max_results=3,
        )
    )
    body = payload.model_dump(mode="json", exclude_none=True)["data"]

    assert "destination" not in body
    assert "max_results" not in body


def test_accommodation_mapper_bounds_and_deduplicates_content() -> None:
    """Customer output should be compact without duplicate amenities or photos."""

    accommodation = create_accommodation_response(
        name="H" * 201,
        description="D" * 1001,
        amenities=[
            {"type": "wifi", "description": "Free Wi-Fi"},
            {"type": "internet", "description": "free wi-fi"},
            {"type": "pool"},
            *[{"type": f"amenity-{index}"} for index in range(25)],
        ],
    )

    hotel = map_duffel_accommodation(accommodation)

    assert len(hotel.name) == 200
    assert hotel.description is not None
    assert len(hotel.description) == 1000
    assert hotel.address == (
        "100 Example Street, Westminster, London, England, SW1A 1AA, GB"
    )
    assert hotel.amenities[:2] == ["Free Wi-Fi", "pool"]
    assert len(hotel.amenities) == 20
    assert hotel.model_dump(mode="json")["photo_urls"] == [
        "https://assets.example.com/hotel-1.jpg",
        "https://assets.example.com/hotel-2.jpg",
        "https://assets.example.com/hotel-3.jpg",
    ]


def test_accommodation_mapper_accepts_missing_optional_content() -> None:
    """A property without description, amenities, or photos remains usable."""

    hotel = map_duffel_accommodation(
        create_accommodation_response(
            description="   ",
            amenities=None,
            photos=[],
        )
    )

    assert hotel.description is None
    assert hotel.amenities == []
    assert hotel.photo_urls == []


def test_stay_result_mapper_preserves_request_party_and_price() -> None:
    """Normalized options should bind provider data to the original party."""

    search = create_resolved_search(
        adults=1,
        children_ages=[4, 8],
    )

    option = map_duffel_stay_result(
        create_search_result_response(),
        search=search,
    )

    assert option.search_result_id == "srr_test_123"
    assert option.guest_count == 3
    assert option.cheapest_total_price == Decimal("799.00")
    assert option.currency == "GBP"
    assert option.price_is_final is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"check_in_date": "2026-09-11"},
            "check-in date does not match",
        ),
        (
            {"check_out_date": "2026-09-13"},
            "checkout date does not match",
        ),
        (
            {"rooms": 2},
            "room count does not match",
        ),
    ],
)
def test_stay_result_mapper_rejects_search_mismatch(
    overrides: dict[str, object],
    message: str,
) -> None:
    """Provider results must describe the dates and rooms that were requested."""

    with pytest.raises(ValueError, match=message):
        map_duffel_stay_result(
            create_search_result_response(**overrides),
            search=create_resolved_search(),
        )


def test_search_response_mapper_filters_sorts_and_limits_results() -> None:
    """Only current cheapest results up to the requested limit should remain."""

    search = create_resolved_search(max_results=2)
    response = create_search_response(
        [
            create_search_result_response(
                id="srr_300",
                cheapest_rate_total_amount="300.00",
            ),
            create_search_result_response(
                id="srr_expired",
                expires_at="2026-08-24T11:59:59Z",
                cheapest_rate_total_amount="50.00",
            ),
            create_search_result_response(
                id="srr_100",
                cheapest_rate_total_amount="100.00",
            ),
            create_search_result_response(
                id="srr_200",
                cheapest_rate_total_amount="200.00",
            ),
        ]
    )

    result = map_duffel_stay_search_response(
        response=response,
        search=search,
        searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )

    assert result.status is HotelSearchStatus.HOTELS_AVAILABLE
    assert [option.search_result_id for option in result.options] == [
        "srr_100",
        "srr_200",
    ]


@pytest.mark.parametrize("include_expired", [False, True])
def test_search_response_mapper_returns_no_hotels_without_current_results(
    include_expired: bool,
) -> None:
    """Empty and fully expired responses should produce a clear empty result."""

    results = (
        [create_search_result_response(expires_at="2026-08-24T12:00:00Z")]
        if include_expired
        else []
    )
    search = create_resolved_search()

    result = map_duffel_stay_search_response(
        response=create_search_response(results),
        search=search,
        searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )

    assert result.status is HotelSearchStatus.NO_HOTELS
    assert result.options == []
    assert result.location == search.location
    assert result.message is not None


def test_search_response_mapper_rejects_naive_searched_at() -> None:
    """Expiry filtering requires an absolute comparison timestamp."""

    with pytest.raises(ValueError, match="searched_at must include a timezone"):
        map_duffel_stay_search_response(
            response=create_search_response([]),
            search=create_resolved_search(),
            searched_at=datetime(2026, 8, 24, 12),
        )


def test_search_response_mapper_rejects_multiple_currencies() -> None:
    """Numerical prices in different currencies cannot be safely sorted."""

    with pytest.raises(ValueError, match="multiple currencies"):
        map_duffel_stay_search_response(
            response=create_search_response(
                [
                    create_search_result_response(
                        id="srr_gbp",
                        cheapest_rate_currency="GBP",
                    ),
                    create_search_result_response(
                        id="srr_usd",
                        cheapest_rate_currency="USD",
                    ),
                ]
            ),
            search=create_resolved_search(),
            searched_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        )
