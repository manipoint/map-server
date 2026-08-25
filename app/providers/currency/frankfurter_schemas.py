"""Validated schemas for Frankfurter API responses."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import CurrencyCode
from app.providers.currency.schemas import CurrencyRate


class FrankfurterRateResponse(BaseModel):
    """One reference exchange rate returned by Frankfurter v2."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    rate_date: date = Field(alias="date")
    base_currency: CurrencyCode = Field(alias="base")
    quote_currency: CurrencyCode = Field(alias="quote")
    rate: CurrencyRate
