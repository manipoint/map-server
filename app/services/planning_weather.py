"""Render provider weather into saved daily notes without model-authored numbers."""

from datetime import UTC, date, timedelta
from zoneinfo import ZoneInfo

from app.domain.itineraries import ItineraryActivity, ItineraryItemType
from app.providers.weather.schemas import HourlyWeatherForecast, WeatherForecast


def _hour_windows(
    hours: tuple[HourlyWeatherForecast, ...],
) -> list[list[HourlyWeatherForecast]]:
    """Merge only consecutive hours with identical probabilities, never average."""
    windows: list[list[HourlyWeatherForecast]] = []
    for hour in hours:
        previous = windows[-1][-1] if windows else None
        if (
            previous is not None
            and hour.local_time.astimezone(UTC) - previous.local_time.astimezone(UTC)
            == timedelta(hours=1)
            and hour.chance_of_rain_percent == previous.chance_of_rain_percent
            and hour.chance_of_snow_percent == previous.chance_of_snow_percent
        ):
            windows[-1].append(hour)
        else:
            windows.append([hour])
    return windows


def itinerary_weather_notes(
    *,
    start_date: date,
    end_date: date,
    forecast: WeatherForecast | None,
    language: str = "en",
) -> list[ItineraryActivity]:
    """Keep unknown dates and probabilities explicit in the existing note contract."""
    days = {day.date: day for day in forecast.days} if forecast else {}
    urdu = language == "ur-Latn"
    notes = []
    for offset in range((end_date - start_date).days + 1):
        trip_date = start_date + timedelta(days=offset)
        day = days.get(trip_date)
        title = ("Mausam" if urdu else "Weather outlook") + f" — {trip_date}"
        if day is None:
            description = (
                "Is tareekh ka verified forecast dastiyab nahi. Safar ke qareeb dobara check karein."
                if urdu
                else "Verified forecast unavailable for this date. Check again closer to travel."
            )
        else:
            lines = [
                f"{day.condition}; {day.min_temperature_c:g}–{day.max_temperature_c:g} °C. "
                f"{forecast.time_zone}. "
                + ("Forecast badal sakta hai." if urdu else "Forecast may change.")
            ]
            if not day.hours:
                lines.append(
                    "Hourly forecast dastiyab nahi."
                    if urdu
                    else "Hourly forecast unavailable."
                )
            else:
                lines.append(
                    "Har listed ghantay ka chance; range ka mushtarka chance nahi. Baqi ghanton ka data nahi:"
                    if urdu
                    else "Chance per listed local hour, not a combined range probability; unlisted hours unavailable:"
                )
                for window in _hour_windows(day.hours):
                    hour = window[0]
                    rain = (
                        "?"
                        if hour.chance_of_rain_percent is None
                        else f"{hour.chance_of_rain_percent}%"
                    )
                    snow = (
                        "?"
                        if hour.chance_of_snow_percent is None
                        else f"{hour.chance_of_snow_percent}%"
                    )
                    local = hour.local_time.strftime("%H:%M %z")
                    if len(window) > 1:
                        end = (
                            window[-1].local_time.astimezone(UTC) + timedelta(hours=1)
                        ).astimezone(ZoneInfo(forecast.time_zone))
                        local += "–" + end.strftime("%H:%M %z")
                    lines.append(
                        f"{local}: {'barish' if urdu else 'rain'} {rain}, {'baraf' if urdu else 'snow'} {snow}"
                    )
                lines.append("? = dastiyab nahi." if urdu else "? = unavailable.")
            description = "\n".join(lines)
        notes.append(
            ItineraryActivity(
                day_number=offset + 1,
                item_type=ItineraryItemType.NOTE,
                title=title,
                description=description,
            )
        )
    return notes
