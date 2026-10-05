"""Shared public image metadata for catalogue-backed assistant content."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class AssistantMedia(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: HttpUrl
    alt_text: str = Field(min_length=1, max_length=300)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> Self:
        if (self.width is None) != (self.height is None):
            raise ValueError("Image width and height must be provided together")
        if self.url.scheme != "https":
            raise ValueError("Image URLs must use HTTPS")
        return self
