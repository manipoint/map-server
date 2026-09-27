"""Provider prices retain currency and exact decimal amounts."""

from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from app.providers.travelport.flight_response_schemas import (
    TravelportCurrencyResponse,
    TravelportPriceResponse,
)


def test_http_json_preserves_decimal_precision():
    response = httpx.Response(
        200,
        text='{"CurrencyCode":{"value":"INR","decimalPlace":2},"TotalPrice":24322.1234567890123456789}',
    )
    result = TravelportPriceResponse.model_validate(response.json(parse_float=Decimal))
    assert result.total_price == Decimal("24322.1234567890123456789")
    assert result.currency.value == "INR"


@pytest.mark.parametrize("places", [0, 2, 3, 9, None])
def test_currency_metadata_does_not_rescale_amount(places):
    result = TravelportPriceResponse.model_validate(
        {
            "CurrencyCode": {"value": " pkr ", "decimalPlace": places},
            "TotalPrice": "123.4500",
            "unknown": True,
        }
    )
    assert result.total_price == Decimal("123.4500")
    assert result.currency.value == "PKR"
    assert (
        TravelportPriceResponse.model_validate_json(
            result.model_dump_json(by_alias=True)
        )
        == result
    )


@pytest.mark.parametrize("amount", [0, "0", "0.01", "999999999999.99"])
def test_nonnegative_prices_are_valid(amount):
    result = TravelportPriceResponse.model_validate(
        {"CurrencyCode": {"value": "USD"}, "TotalPrice": amount}
    )
    assert result.total_price == Decimal(str(amount))


@pytest.mark.parametrize(
    "amount", [-1, "-0.01", "NaN", "Infinity", "-Infinity", None, "bad", True, {}, []]
)
def test_invalid_prices_are_rejected(amount):
    with pytest.raises(ValidationError):
        TravelportPriceResponse.model_validate(
            {"CurrencyCode": {"value": "USD"}, "TotalPrice": amount}
        )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"TotalPrice": "10"},
        {"CurrencyCode": {"value": "USD"}},
        {"TotalPrice": "10", "CurrencyCode": None},
        {"TotalPrice": "10", "CurrencyCode": {}},
    ],
)
def test_missing_price_or_currency_is_not_invented(data):
    with pytest.raises(ValidationError):
        TravelportPriceResponse.model_validate(data)


@pytest.mark.parametrize("value", ["", "US", "USDD", "U1D", None])
def test_invalid_currency_code_is_rejected(value):
    with pytest.raises(ValidationError):
        TravelportCurrencyResponse(value=value)


@pytest.mark.parametrize("places", [-1, 10, 1.5])
def test_invalid_currency_precision_is_rejected(places):
    with pytest.raises(ValidationError):
        TravelportCurrencyResponse(value="USD", decimal_place=places)
