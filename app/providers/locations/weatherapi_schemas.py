"""WeatherAPI location-search response schemas."""

from pydantic import BaseModel, ConfigDict, Field


class WeatherApiLocationCandidate(BaseModel):
    """One location candidate returned by WeatherAPI search."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    name: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    country: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
