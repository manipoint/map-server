"""Normalized weather-provider data models."""

from datetime import datetime

from pydantic import BaseModel


class CurrentWeather(BaseModel):
    """Current conditions normalized independently of a weather vendor."""

    location: str
    country: str | None = None
    observed_at: datetime
    condition: str
    temperature_c: float
    feels_like_c: float | None = None
    humidity_percent: int | None = None
    wind_kph: float | None = None
