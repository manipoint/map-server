"""Tests for hotel-search domain values."""

from app.domain.hotels import HotelSearchStatus


def test_hotel_search_status_values_are_stable() -> None:
    """Hotel outcomes should remain safe for APIs and persisted messages."""

    assert [status.value for status in HotelSearchStatus] == [
        "hotels_available",
        "no_hotels",
    ]
