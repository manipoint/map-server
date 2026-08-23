"""Flight domain models."""

from enum import StrEnum


class FlightCabinClass(StrEnum):
    """Supported flight cabin classes."""

    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class FlightSearchStatus(StrEnum):
    """Normalized outcomes of a flight search."""

    OFFERS_AVAILABLE = "offers_available"
    NO_OFFERS = "no_offers"
    GROUP_BOOKING_REQUIRED = "group_booking_required"
