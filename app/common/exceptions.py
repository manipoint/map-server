"""Shared application exceptions."""


class ProviderError(Exception):
    """Base error for safe external-provider failures."""


class ProviderConfigurationError(ProviderError):
    """Raised when required provider configuration is missing or invalid."""


class ProviderUnavailableError(ProviderError):
    """Raised when a provider cannot return a usable response."""
