"""Reusable validated scalar values shared across travel domains."""

from typing import Annotated

from pydantic import BeforeValidator, Field, StringConstraints

ChildAge = Annotated[int, Field(ge=2, le=17)]
InfantAge = Annotated[int, Field(ge=0, le=1)]
MinorAge = Annotated[int, Field(ge=0, le=17)]
StrictMinorAge = Annotated[MinorAge, Field(strict=True)]
Interest = Annotated[str, Field(min_length=1, max_length=60)]


def normalize_upper_code(value: object) -> object:
    """Trim and uppercase string codes before constraint validation."""

    if isinstance(value, str):
        return value.strip().upper()
    return value


CurrencyCode = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
    BeforeValidator(normalize_upper_code),
]

CountryCode = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
    ),
    BeforeValidator(normalize_upper_code),
]
IataCode = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
    BeforeValidator(normalize_upper_code),
]
