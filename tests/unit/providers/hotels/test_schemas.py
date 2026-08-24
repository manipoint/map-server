"""Tests for provider-independent hotel-search schemas."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.hotels import HotelSearchStatus
from app.providers.hotels.schemas import (
    HotelProperty,
    HotelSearchInput,
    HotelSearchOption,
    HotelSearchResult,
    ResolvedHotelSearch,
)
from app.providers.locations.schemas import ResolvedLocation


def create_search(**overrides: object) -> HotelSearchInput:
    """Create one valid hotel search with optional overrides."""

    values: dict[str, object] = {
        "destination": "Lahore",
        "check_in_date": date(2026, 9, 10),
        "check_out_date": date(2026, 9, 12),
    }
    values.update(overrides)
    return HotelSearchInput(**values)


def create_hotel(**overrides: object) -> HotelProperty:
    """Create one compact normalized hotel with optional overrides."""

    values: dict[str, object] = {
        "accommodation_id": "acc_test_123",
        "name": "Example Hotel",
        "description": "A central hotel.",
        "rating": 4,
        "review_score": "8.8",
        "review_count": 336,
        "address": "100 Example Street",
        "city_name": "London",
        "country_code": "GB",
        "latitude": 51.5071,
        "longitude": -0.1416,
        "amenities": ["Wi-Fi", "Breakfast"],
        "photo_urls": [
            "https://assets.example.com/hotel-1.jpg",
            "https://assets.example.com/hotel-2.jpg",
            "https://assets.example.com/hotel-3.jpg",
        ],
    }
    values.update(overrides)
    return HotelProperty(**values)


def create_option(**overrides: object) -> HotelSearchOption:
    """Create one current normalized hotel option with optional overrides."""

    values: dict[str, object] = {
        "search_result_id": "srr_test_123",
        "hotel": create_hotel(),
        "check_in_date": date(2026, 9, 10),
        "check_out_date": date(2026, 9, 12),
        "rooms": 1,
        "guest_count": 2,
        "cheapest_total_price": "799.00",
        "currency": "GBP",
        "expires_at": datetime(2026, 9, 1, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return HotelSearchOption(**values)


def create_location() -> ResolvedLocation:
    """Create one resolved London location for output tests."""

    return ResolvedLocation(
        query="London",
        display_name="London, United Kingdom",
        latitude=51.5071,
        longitude=-0.1276,
    )


def test_hotel_search_normalizes_destination_and_computes_party() -> None:
    """A normal family request should retain every guest and stay night."""

    request = create_search(
        destination="  Lahore  ",
        adults=1,
        children_ages=[4, 8],
    )

    assert request.destination == "Lahore"
    assert request.nights == 2
    assert request.total_guests == 3
    assert request.rooms == 1
    assert request.max_results == 5


@pytest.mark.parametrize(
    "check_out_date",
    [date(2026, 9, 10), date(2026, 9, 9)],
)
def test_hotel_search_requires_checkout_after_checkin(
    check_out_date: date,
) -> None:
    """Zero-night and backwards stays should fail before provider work."""

    with pytest.raises(ValidationError, match="must be after"):
        create_search(check_out_date=check_out_date)


def test_hotel_search_accepts_exactly_ninety_nine_nights() -> None:
    """Duffel's documented maximum stay should remain valid."""

    check_in = date(2026, 9, 10)
    request = create_search(
        check_in_date=check_in,
        check_out_date=check_in + timedelta(days=99),
    )

    assert request.nights == 99


def test_hotel_search_rejects_more_than_ninety_nine_nights() -> None:
    """A stay longer than Duffel supports should fail locally."""

    check_in = date(2026, 9, 10)

    with pytest.raises(ValidationError, match="cannot exceed 99 nights"):
        create_search(
            check_in_date=check_in,
            check_out_date=check_in + timedelta(days=100),
        )


def test_hotel_search_requires_one_adult_per_room() -> None:
    """Every requested room must be assignable to at least one adult."""

    with pytest.raises(ValidationError, match="each room requires"):
        create_search(adults=2, rooms=3)


def test_hotel_search_does_not_apply_arbitrary_group_limit() -> None:
    """A large office group should remain valid when every room has an adult."""

    request = create_search(
        adults=35,
        children_ages=[5, 10, 15],
        rooms=20,
        max_results=10,
    )

    assert request.total_guests == 38
    assert request.rooms == 20


@pytest.mark.parametrize("child_age", [-1, 18])
def test_hotel_search_rejects_invalid_child_age(child_age: int) -> None:
    """Hotel children must carry an exact age between zero and seventeen."""

    with pytest.raises(ValidationError):
        create_search(children_ages=[child_age])


def test_hotel_search_rejects_blank_destination_and_extra_fields() -> None:
    """Ambiguous or unsupported input should not reach geocoding."""

    with pytest.raises(ValidationError):
        create_search(destination="   ")

    with pytest.raises(ValidationError):
        HotelSearchInput.model_validate(
            {
                "destination": "Lahore",
                "check_in_date": "2026-09-10",
                "check_out_date": "2026-09-12",
                "unsupported": True,
            }
        )


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(-90.0, -180.0), (90.0, 180.0)],
)
def test_resolved_location_accepts_coordinate_boundaries(
    latitude: float,
    longitude: float,
) -> None:
    """Valid global coordinate edges should remain representable."""

    location = ResolvedLocation(
        query="Henderson Island",
        display_name="Henderson Island, Pitcairn Islands",
        latitude=latitude,
        longitude=longitude,
    )

    assert location.latitude == latitude
    assert location.longitude == longitude


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(-90.1, 0.0), (90.1, 0.0), (0.0, -180.1), (0.0, 180.1)],
)
def test_resolved_location_rejects_out_of_range_coordinates(
    latitude: float,
    longitude: float,
) -> None:
    """Invalid coordinates must not be sent to Duffel."""

    with pytest.raises(ValidationError):
        ResolvedLocation(
            query="Lahore",
            display_name="Lahore, Pakistan",
            latitude=latitude,
            longitude=longitude,
        )


def test_resolved_hotel_search_combines_request_location_and_radius() -> None:
    """Provider input should bind the original request to resolved coordinates."""

    search = ResolvedHotelSearch(
        request=create_search(),
        location=ResolvedLocation(
            query="Lahore",
            display_name="Lahore, Pakistan",
            latitude=31.5204,
            longitude=74.3587,
        ),
        radius_km=5,
    )

    assert search.request.destination == "Lahore"
    assert search.location.display_name == "Lahore, Pakistan"
    assert search.radius_km == 5


def test_hotel_property_preserves_compact_customer_visible_fields() -> None:
    """Normalized properties should retain location, ratings, and three photos."""

    hotel = create_hotel(country_code=" gb ")

    assert hotel.country_code == "GB"
    assert hotel.review_score == Decimal("8.8")
    assert hotel.amenities == ["Wi-Fi", "Breakfast"]
    assert hotel.model_dump(mode="json")["photo_urls"] == [
        "https://assets.example.com/hotel-1.jpg",
        "https://assets.example.com/hotel-2.jpg",
        "https://assets.example.com/hotel-3.jpg",
    ]


@pytest.mark.parametrize(
    "photo_urls",
    [
        [],
        ["https://assets.example.com/hotel-1.jpg"],
        [
            "https://assets.example.com/hotel-1.jpg",
            "https://assets.example.com/hotel-2.jpg",
            "https://assets.example.com/hotel-3.jpg",
        ],
    ],
)
def test_hotel_property_accepts_up_to_three_photos(
    photo_urls: list[str],
) -> None:
    """Providers may return fewer than three usable accommodation photos."""

    hotel = create_hotel(photo_urls=photo_urls)

    assert len(hotel.photo_urls) == len(photo_urls)


def test_hotel_property_rejects_more_than_three_photos() -> None:
    """Normalized responses stay compact for WebSocket and model contexts."""

    with pytest.raises(ValidationError):
        create_hotel(
            photo_urls=[
                f"https://assets.example.com/hotel-{index}.jpg" for index in range(4)
            ]
        )


def test_hotel_property_rejects_invalid_photo_url() -> None:
    """Malformed provider photo URLs must not reach Flutter clients."""

    with pytest.raises(ValidationError):
        create_hotel(photo_urls=["not-a-url"])


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("name", " "),
        ("city_name", " "),
        ("country_code", "GBR"),
        ("rating", 6),
        ("review_score", "10.1"),
        ("review_count", -1),
        ("latitude", 90.1),
        ("longitude", 180.1),
    ],
)
def test_hotel_property_rejects_invalid_provider_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    """Malformed hotel content should fail before reaching the model."""

    with pytest.raises(ValidationError):
        create_hotel(**{field_name: invalid_value})


def test_hotel_option_preserves_non_final_search_price() -> None:
    """The cheapest search price must remain exact and explicitly non-final."""

    option = create_option(currency=" gbp ")

    assert option.cheapest_total_price == Decimal("799.00")
    assert option.currency == "GBP"
    assert option.price_is_final is False
    assert option.expires_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"check_out_date": date(2026, 9, 10)},
            "checkout must be after",
        ),
        (
            {"rooms": 3, "guest_count": 2},
            "at least one guest per room",
        ),
        (
            {"expires_at": datetime(2026, 9, 1, 12)},
            "expires_at must include a timezone",
        ),
    ],
)
def test_hotel_option_rejects_inconsistent_values(
    overrides: dict[str, object],
    message: str,
) -> None:
    """Invalid normalized options must not be shown to users."""

    with pytest.raises(ValidationError, match=message):
        create_option(**overrides)


def test_hotel_option_cannot_claim_search_price_is_final() -> None:
    """Only the future quote flow may represent a final hotel price."""

    with pytest.raises(ValidationError):
        create_option(price_is_final=True)


def test_available_hotel_result_requires_options() -> None:
    """An available status without hotels is internally inconsistent."""

    with pytest.raises(ValidationError, match="requires at least one option"):
        HotelSearchResult(
            status=HotelSearchStatus.HOTELS_AVAILABLE,
            searched_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
            location=create_location(),
        )


def test_no_hotel_result_rejects_options() -> None:
    """A no-hotels result cannot simultaneously contain an available option."""

    with pytest.raises(ValidationError, match="cannot contain options"):
        HotelSearchResult(
            status=HotelSearchStatus.NO_HOTELS,
            searched_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
            location=create_location(),
            options=[create_option()],
        )


def test_hotel_result_accepts_bounded_available_options() -> None:
    """A current hotel result should retain its location and normalized options."""

    result = HotelSearchResult(
        status=HotelSearchStatus.HOTELS_AVAILABLE,
        searched_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        location=create_location(),
        options=[create_option()],
    )

    assert result.options[0].hotel.name == "Example Hotel"
    assert result.location.display_name == "London, United Kingdom"


def test_hotel_result_rejects_naive_search_time() -> None:
    """Search timestamps must be absolute for expiry filtering."""

    with pytest.raises(ValidationError, match="searched_at must include a timezone"):
        HotelSearchResult(
            status=HotelSearchStatus.NO_HOTELS,
            searched_at=datetime(2026, 9, 1, 12),
            location=create_location(),
        )


def test_hotel_result_rejects_more_than_ten_options() -> None:
    """Unexpected output must stay inside the model payload boundary."""

    with pytest.raises(ValidationError):
        HotelSearchResult(
            status=HotelSearchStatus.HOTELS_AVAILABLE,
            searched_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
            location=create_location(),
            options=[
                create_option(search_result_id=f"srr_{index}") for index in range(11)
            ],
        )
