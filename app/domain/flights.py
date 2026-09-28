"""Flight domain models."""

from enum import StrEnum

ONE_WAY_ONLY_MESSAGE = (
    "Only one-way flight searches are currently supported. "
    "Ask whether the user wants an outbound-only search. "
    "Do not discard the return date or split the journey without confirmation."
)


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
