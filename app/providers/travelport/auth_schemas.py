"""Validated Travelport authentication responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

DEFAULT_TOKEN_LIFETIME_SECONDS = 86_400


class TravelportTokenResponse(BaseModel):
    """Parse only the fields required for token caching."""

    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )
    access_token: SecretStr
    token_type: Literal["Bearer", "bearer"] = "Bearer"
    expires_in: int = Field(
        default=DEFAULT_TOKEN_LIFETIME_SECONDS,
        gt=0,
    )

    @field_validator("access_token")
    @classmethod
    def validate_access_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not token or any(character.isspace() for character in token):
            raise ValueError("Invalid access token")
        return value
