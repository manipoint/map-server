"""Normalized weather-provider data models."""

from datetime import UTC, date, datetime
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


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


class HourlyWeatherForecast(BaseModel):
    """One forecast hour in the destination's local time."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    local_time: AwareDatetime
    condition: str = Field(min_length=1, max_length=100)
    temperature_c: float
    chance_of_rain_percent: int | None = Field(default=None, ge=0, le=100)
    chance_of_snow_percent: int | None = Field(default=None, ge=0, le=100)
    precipitation_mm: float | None = Field(default=None, ge=0)
    snow_cm: float | None = Field(default=None, ge=0)


class DailyWeatherForecast(BaseModel):
    """A forecast day with bounded hourly precipitation details."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    date: date
    condition: str = Field(min_length=1, max_length=100)
    max_temperature_c: float
    min_temperature_c: float
    total_precipitation_mm: float = Field(ge=0)
    total_snow_cm: float | None = Field(default=None, ge=0)
    # A daylight-saving transition can have 23 or 25 distinct forecast hours.
    hours: tuple[HourlyWeatherForecast, ...] = Field(max_length=25)

    @model_validator(mode="after")
    def validate_hours(self) -> Self:
        if self.min_temperature_c > self.max_temperature_c:
            raise ValueError("Minimum temperature exceeds maximum")
        instants = [hour.local_time.astimezone(UTC) for hour in self.hours]
        if instants != sorted(set(instants)):
            raise ValueError("Forecast hours must be unique and ordered")
        if any(hour.local_time.date() != self.date for hour in self.hours):
            raise ValueError("Forecast hour does not match its day")
        return self


class WeatherForecast(BaseModel):
    """Forecast coverage returned by the provider for a requested date range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    location: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=100)
    time_zone: str = Field(min_length=1, max_length=100)
    days: tuple[DailyWeatherForecast, ...] = Field(max_length=14)

    @field_validator("time_zone")
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Invalid forecast time zone") from error
        return value

    @model_validator(mode="after")
    def validate_days(self) -> Self:
        dates = [day.date for day in self.days]
        if dates != sorted(set(dates)):
            raise ValueError("Forecast days must be unique and ordered")
        zone = ZoneInfo(self.time_zone)
        for day in self.days:
            for hour in day.hours:
                local = hour.local_time.astimezone(zone)
                if (
                    local.date() != day.date
                    or local.utcoffset() != hour.local_time.utcoffset()
                ):
                    raise ValueError(
                        "Forecast hour does not match destination time zone"
                    )
        return self
