"""Flight domain models."""

from enum import StrEnum


class FlightCabinClass(StrEnum):
    """Supported flight cabin classes."""

    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"
