"""Tests for reusable validated travel value objects."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.value_objects import CountryCode, CurrencyCode

_CURRENCY_ADAPTER = TypeAdapter(CurrencyCode)
_COUNTRY_ADAPTER = TypeAdapter(CountryCode)


@pytest.mark.parametrize("value", ["USD", " usd ", "uSd"])
def test_currency_code_normalizes_valid_values(value: str) -> None:
    """Currency values should use one canonical uppercase representation."""

    assert _CURRENCY_ADAPTER.validate_python(value) == "USD"


@pytest.mark.parametrize("value", ["US", "USDD", "U1D", "   ", 123])
def test_currency_code_rejects_invalid_values(value: object) -> None:
    """Only three-letter alphabetic currency codes should be accepted."""

    with pytest.raises(ValidationError):
        _CURRENCY_ADAPTER.validate_python(value)


@pytest.mark.parametrize("value", ["GB", " gb ", "gB"])
def test_country_code_normalizes_valid_values(value: str) -> None:
    """Country values should use one canonical uppercase representation."""

    assert _COUNTRY_ADAPTER.validate_python(value) == "GB"


@pytest.mark.parametrize("value", ["G", "GBR", "G1", "   ", 12])
def test_country_code_rejects_invalid_values(value: object) -> None:
    """Only two-letter alphabetic country codes should be accepted."""

    with pytest.raises(ValidationError):
        _COUNTRY_ADAPTER.validate_python(value)
