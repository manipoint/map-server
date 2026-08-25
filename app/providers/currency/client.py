"""Currency-conversion provider contract."""

from typing import Protocol

from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)


class CurrencyProvider(Protocol):
    """Contract implemented by every external currency provider."""

    async def convert_currency(
        self,
        *,
        request: CurrencyConversionInput,
    ) -> CurrencyConversionResult:
        """Convert an amount using a normalized reference exchange rate."""
