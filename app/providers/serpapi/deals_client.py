"""Normalize flexible-date flight deals returned by SerpApi."""

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.config import Settings
from app.providers.serpapi.client import SerpApiClient


class FlightDeal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str
    destination: str
    start_date: date
    end_date: date
    price: Decimal = Field(ge=0)
    currency: str
    airline: str | None = None
    stops: int | None = Field(default=None, ge=0)
    flight_link: str

    @property
    def duration_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @model_validator(mode="after")
    def check_dates(self) -> "FlightDeal":
        if self.end_date < self.start_date:
            raise ValueError("Deal return date precedes departure date")
        return self


class SerpApiDealsClient:
    def __init__(self, *, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = SerpApiClient(http_client=http_client, settings=settings)

    async def search_deals(
        self,
        *,
        origin_code: str,
        destination_codes: set[str],
        window_start: date,
        window_end: date,
        currency: str,
        adults: int,
        children: int = 0,
        infants_in_seat: int = 0,
        infants_in_lap: int = 0,
    ) -> list[FlightDeal]:
        """Return deals for the selected route whose full trip fits the window."""
        if window_end < window_start or (window_end - window_start).days > 30:
            raise ValueError("Deal search window must contain 1–31 days")
        if not destination_codes:
            raise ValueError("At least one destination airport is required")
        if adults < 1 or min(children, infants_in_seat, infants_in_lap) < 0:
            raise ValueError("Traveler counts are invalid")

        payload = await self.client.search(
            engine="google_flights_deals",
            params={
                "departure_id": origin_code,
                "outbound_date": (
                    f"{window_start.isoformat()},{window_end.isoformat()}"
                ),
                "currency": currency,
                "adults": adults,
                "children": children,
                "infants_in_seat": infants_in_seat,
                "infants_on_lap": infants_in_lap,
            },
        )

        deals: list[FlightDeal] = []
        for row in payload.get("deals", []):
            if not isinstance(row, dict):
                continue
            if row.get("departure_airport_code") != origin_code:
                continue
            if row.get("arrival_airport_code") not in destination_codes:
                continue

            try:
                link = row["flight_link"]
                if not isinstance(link, str):
                    continue
                parsed_link = urlsplit(link)
                if parsed_link.scheme != "https" or parsed_link.hostname not in {
                    "google.com",
                    "www.google.com",
                }:
                    continue

                deal = FlightDeal(
                    origin=row["departure_airport_code"],
                    destination=row["arrival_airport_code"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    price=Decimal(str(row["price"])),
                    currency=currency,
                    airline=row.get("airline"),
                    stops=row.get("stops"),
                    flight_link=link,
                )
            except (KeyError, TypeError, ValueError, InvalidOperation, ValidationError):
                continue

            if window_start <= deal.start_date and deal.end_date <= window_end:
                deals.append(deal)

        deals.sort(key=lambda deal: (deal.price, deal.start_date, deal.end_date))
        return deals
