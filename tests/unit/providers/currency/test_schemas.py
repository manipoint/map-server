"""Tests for provider-independent currency-conversion schemas."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)


def create_result(**overrides: object) -> CurrencyConversionResult:
    """Create one valid conversion result with optional overrides."""

    values: dict[str, object] = {
        "amount": "100.25",
        "base_currency": "USD",
        "quote_currency": "PKR",
        "rate": "278.451234",
        "converted_amount": "27914.73",
        "rate_date": date(2026, 8, 24),
        "observed_at": datetime(2026, 8, 25, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return CurrencyConversionResult(**values)


def test_conversion_input_normalizes_codes_and_preserves_decimal_amount() -> None:
    """Currency codes should normalize without losing monetary precision."""

    request = CurrencyConversionInput(
        amount="125.375",
        base_currency=" usd ",
        quote_currency=" pkr ",
    )

    assert request.amount == Decimal("125.375")
    assert request.base_currency == "USD"
    assert request.quote_currency == "PKR"


def test_conversion_input_accepts_zero_amount() -> None:
    """A zero-priced item should remain representable without special casing."""

    request = CurrencyConversionInput(
        amount=0,
        base_currency="USD",
        quote_currency="EUR",
    )

    assert request.amount == Decimal("0")


@pytest.mark.parametrize("amount", ["-0.01", "1.1234567"])
def test_conversion_input_rejects_invalid_amount(amount: str) -> None:
    """Negative or over-precise amounts should fail before provider work."""

    with pytest.raises(ValidationError):
        CurrencyConversionInput(
            amount=amount,
            base_currency="USD",
            quote_currency="PKR",
        )


@pytest.mark.parametrize("currency", ["US", "USDD", "U$D"])
def test_conversion_input_rejects_invalid_currency_code(currency: str) -> None:
    """Only normalized three-letter currency codes should be accepted."""

    with pytest.raises(ValidationError):
        CurrencyConversionInput(
            amount="10.00",
            base_currency=currency,
            quote_currency="PKR",
        )


def test_conversion_input_forbids_unknown_fields() -> None:
    """Unexpected model-generated arguments must not reach a provider."""

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CurrencyConversionInput.model_validate(
            {
                "amount": "10.00",
                "base_currency": "USD",
                "quote_currency": "PKR",
                "round_up": True,
            }
        )


def test_conversion_result_preserves_rate_and_aware_observation_time() -> None:
    """A provider quote should retain exact decimals and its observation time."""

    result = create_result()

    assert result.rate == Decimal("278.451234")
    assert result.converted_amount == Decimal("27914.73")
    assert result.rate_date == date(2026, 8, 24)
    assert result.observed_at.utcoffset() is not None


def test_conversion_result_serializes_decimals_without_float_loss() -> None:
    """JSON output should represent monetary decimals as strings."""

    payload = create_result().model_dump(mode="json")

    assert payload["amount"] == "100.25"
    assert payload["rate"] == "278.451234"
    assert payload["converted_amount"] == "27914.73"


def test_conversion_result_rejects_naive_observation_time() -> None:
    """A rate without an absolute observation instant is unsafe to cache."""

    with pytest.raises(ValidationError, match="must include a timezone"):
        create_result(observed_at=datetime(2026, 8, 25, 12))


@pytest.mark.parametrize("rate", ["0", "-1"])
def test_conversion_result_requires_positive_rate(rate: str) -> None:
    """Zero and negative exchange rates are invalid provider data."""

    with pytest.raises(ValidationError):
        create_result(rate=rate)


def test_same_currency_conversion_requires_identity_rate() -> None:
    """A same-currency conversion must not fabricate an exchange rate."""

    with pytest.raises(ValidationError, match="requires a rate of 1"):
        create_result(
            base_currency="USD",
            quote_currency="USD",
            rate="1.01",
            converted_amount="100.25",
        )


def test_same_currency_conversion_must_preserve_amount() -> None:
    """A same-currency conversion must not alter the original amount."""

    with pytest.raises(ValidationError, match="preserve the original amount"):
        create_result(
            base_currency="USD",
            quote_currency="USD",
            rate="1",
            converted_amount="100.24",
        )


def test_same_currency_identity_conversion_is_valid() -> None:
    """The service may answer an identity conversion without provider cost."""

    result = create_result(
        base_currency="USD",
        quote_currency="USD",
        rate="1",
        converted_amount="100.25",
    )

    assert result.rate == Decimal("1")
    assert result.converted_amount == result.amount


def test_cross_currency_result_allows_provider_rounding() -> None:
    """Converted output may follow target-currency rounding rules."""

    result = create_result(
        amount="10",
        rate="0.333333",
        converted_amount="3.33",
    )

    assert result.converted_amount == Decimal("3.33")
