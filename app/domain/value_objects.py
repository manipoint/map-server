"""Reusable validated scalar values shared across travel domains."""

from typing import Annotated

from pydantic import BeforeValidator, StringConstraints


def normalize_upper_code(value: object) -> object:
    """Trim and uppercase string codes before constraint validation."""

    if isinstance(value, str):
        return value.strip().upper()
    return value


CurrencyCode = Annotated[
    str,
    BeforeValidator(normalize_upper_code),
    StringConstraints(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
]

CountryCode = Annotated[
    str,
    BeforeValidator(normalize_upper_code),
    StringConstraints(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
    ),
]
