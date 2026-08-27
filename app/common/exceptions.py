"""Shared application exceptions."""


class ProviderError(Exception):
    """Base error for safe external-provider failures."""


class ProviderConfigurationError(ProviderError):
    """Raised when required provider configuration is missing or invalid."""


class ProviderUnavailableError(ProviderError):
    """Raised when a provider cannot return a usable response."""


class CurrencyPairUnavailableError(ProviderError):
    """Raised when no reference rate exists for a requested currency pair."""


class LocationResolutionError(Exception):
    """Base error for unresolved user destinations."""


class LocationNotFoundError(LocationResolutionError):
    """Raised when no location matches the destination."""


class AmbiguousLocationError(LocationResolutionError):
    """Raised when the user must select between location candidates."""

    def __init__(self, *, candidates: list[str]) -> None:
        super().__init__("Multiple locations matched the destination")
        self.candidates = tuple(candidates)


class InvalidTravelDateError(Exception):
    """Raised when travel dates cannot be searched."""


class InvalidCursorError(ValueError):
    """Raised when a pagination cursor cannot be safely decoded."""
