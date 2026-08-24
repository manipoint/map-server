"""Provider-independent location schemas."""

from pydantic import BaseModel, ConfigDict, Field


class ResolvedLocation(BaseModel):
    """A deterministic geocoding result for a destination."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=120)
    display_name: str = Field(min_length=2, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
