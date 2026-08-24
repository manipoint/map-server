"""Place domain models."""

from enum import StrEnum


class PlaceSearchStatus(StrEnum):
    """Normalized outcome of a places search."""

    PLACES_AVAILABLE = "places_available"
    NO_PLACES = "no_places"
