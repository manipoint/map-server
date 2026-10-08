"""Weather MCP tool schemas."""

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.itineraries import MAX_ITINERARY_DAYS


class CurrentWeatherInput(BaseModel):
    """Validated input for the current-weather MCP tool."""

    model_config = ConfigDict(str_strip_whitespace=True)

    city: str = Field(min_length=1, max_length=120)


class WeatherForecastInput(BaseModel):
    """Bounded destination and date range for an hourly forecast lookup."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    city: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if (self.end_date - self.start_date).days >= MAX_ITINERARY_DAYS:
            raise ValueError("weather request exceeds maximum trip duration")
        return self
