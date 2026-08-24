"""Hotel-search domain values."""

from enum import StrEnum


class HotelSearchStatus(StrEnum):
    """Normalized availability outcome for a hotel search."""

    HOTELS_AVAILABLE = "hotels_available"
    NO_HOTELS = "no_hotels"
