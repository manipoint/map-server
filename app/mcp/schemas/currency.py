"""Currency-conversion MCP schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.providers.currency.schemas import CurrencyConversionInput


class CurrencyConversionGuidance(BaseModel):
    """User guidance when a requested reference-rate pair is unavailable."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["pair_unavailable"] = "pair_unavailable"
    message: str = Field(min_length=1, max_length=300)


__all__ = ["CurrencyConversionGuidance", "CurrencyConversionInput"]
