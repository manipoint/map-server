"""Currency-conversion MCP tool registration."""

from fastmcp import FastMCP

from app.common.exceptions import CurrencyPairUnavailableError
from app.domain.value_objects import CurrencyCode
from app.mcp.schemas.currency import CurrencyConversionGuidance
from app.providers.currency.client import CurrencyProvider
from app.providers.currency.schemas import (
    CurrencyAmount,
    CurrencyConversionInput,
    CurrencyConversionResult,
)


def register_currency_tools(
    server: FastMCP,
    *,
    currency_provider: CurrencyProvider,
) -> None:
    """Register normalized currency-conversion tools on an MCP server."""

    @server.tool(
        name="convert_currency",
        description=(
            "Convert one non-negative monetary amount using a current reference "
            "exchange rate. Use three-letter currency codes. The result includes "
            "the provider rate date and observation time. Reference only; not a "
            "payment or booking quote."
        ),
    )
    async def convert_currency(
        amount: CurrencyAmount,
        base_currency: CurrencyCode,
        quote_currency: CurrencyCode,
    ) -> CurrencyConversionResult | CurrencyConversionGuidance:
        """Validate and execute one currency conversion."""

        request = CurrencyConversionInput(
            amount=amount,
            base_currency=base_currency,
            quote_currency=quote_currency,
        )
        try:
            return await currency_provider.convert_currency(request=request)
        except CurrencyPairUnavailableError:
            return CurrencyConversionGuidance(
                message=(
                    "No reference rate is available for that currency pair. "
                    "Please verify both three-letter currency codes."
                )
            )
