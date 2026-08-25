"""Provider-independent currency-conversion schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.value_objects import CurrencyCode

CurrencyAmount = Annotated[
    Decimal,
    Field(ge=0, max_digits=24, decimal_places=6),
]
CurrencyRate = Annotated[
    Decimal,
    Field(gt=0, max_digits=30, decimal_places=12),
]


class CurrencyConversionInput(BaseModel):
    """Validated provider-independent currency-conversion request."""

    model_config = ConfigDict(extra="forbid")

    amount: CurrencyAmount
    base_currency: CurrencyCode
    quote_currency: CurrencyCode


class CurrencyConversionResult(BaseModel):
    """One normalized conversion based on an observed exchange rate."""

    model_config = ConfigDict(extra="forbid")

    amount: CurrencyAmount
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: CurrencyRate
    converted_amount: CurrencyAmount
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        """Require an absolute instant for the exchange-rate observation."""

        if value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_identity_conversion(self) -> Self:
        """Keep same-currency conversions exact and provider-independent."""

        if self.base_currency != self.quote_currency:
            return self

        if self.rate != Decimal("1"):
            raise ValueError("same-currency conversion requires a rate of 1")

        if self.converted_amount != self.amount:
            raise ValueError(
                "same-currency conversion must preserve the original amount"
            )

        return self


__all__ = [
    "CurrencyAmount",
    "CurrencyConversionInput",
    "CurrencyConversionResult",
    "CurrencyRate",
]
