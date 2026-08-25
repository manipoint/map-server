"""Frankfurter v2 currency-conversion adapter."""

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

import httpx
from pydantic import ValidationError

from app.common.exceptions import (
    CurrencyPairUnavailableError,
    ProviderConfigurationError,
    ProviderUnavailableError,
)
from app.common.time import UtcClock, utc_now
from app.config import Settings
from app.providers.currency.frankfurter_schemas import FrankfurterRateResponse
from app.providers.currency.schemas import (
    CurrencyConversionInput,
    CurrencyConversionResult,
)

CONVERSION_QUANTUM: Final = Decimal("0.000001")
_PAIR_ERROR_STATUS_CODES: Final = frozenset({400, 404, 422})


class FrankfurterCurrencyClient:
    """Convert currencies using keyless Frankfurter reference rates."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: UtcClock = utc_now,
    ) -> None:
        if settings.currency_provider != "frankfurter":
            raise ProviderConfigurationError(
                "Frankfurter currency provider is not configured"
            )

        self.http_client = http_client
        self.settings = settings
        self.clock = clock

    async def convert_currency(
        self,
        *,
        request: CurrencyConversionInput,
    ) -> CurrencyConversionResult:
        """Convert one amount using one latest pair-rate request."""

        if request.base_currency == request.quote_currency:
            observed_at = self.clock()
            return CurrencyConversionResult(
                amount=request.amount,
                base_currency=request.base_currency,
                quote_currency=request.quote_currency,
                rate=Decimal("1"),
                converted_amount=request.amount,
                rate_date=observed_at.date(),
                observed_at=observed_at,
            )

        base_url = self.settings.frankfurter_base_url.rstrip("/")
        url = f"{base_url}/rate/{request.base_currency}/{request.quote_currency}"

        try:
            response = await self.http_client.get(
                url,
                headers={"Accept": "application/json"},
                timeout=self.settings.provider_timeout_seconds,
            )
            response.raise_for_status()
            provider_rate = FrankfurterRateResponse.model_validate(
                response.json(parse_float=Decimal)
            )

            if (
                provider_rate.base_currency != request.base_currency
                or provider_rate.quote_currency != request.quote_currency
            ):
                raise ValueError("currency pair does not match the request")

            converted_amount = (request.amount * provider_rate.rate).quantize(
                CONVERSION_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
            observed_at = self.clock()

            return CurrencyConversionResult(
                amount=request.amount,
                base_currency=request.base_currency,
                quote_currency=request.quote_currency,
                rate=provider_rate.rate,
                converted_amount=converted_amount,
                rate_date=provider_rate.rate_date,
                observed_at=observed_at,
            )

        except httpx.HTTPStatusError as error:
            if error.response.status_code in _PAIR_ERROR_STATUS_CODES:
                raise CurrencyPairUnavailableError(
                    "Currency pair is unavailable"
                ) from error

            raise ProviderUnavailableError(
                "Currency provider is unavailable"
            ) from error

        except httpx.HTTPError as error:
            raise ProviderUnavailableError(
                "Currency provider is unavailable"
            ) from error

        except (ArithmeticError, TypeError, ValueError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Currency provider returned an invalid response"
            ) from error
