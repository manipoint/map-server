"""SerpApi Google Hotels adapter."""

import hashlib
import json
from datetime import UTC, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.common.exceptions import UnsupportedHotelRequestError
from app.common.time import UtcClock, utc_now
from app.config import Settings
from app.domain.hotels import HotelSearchStatus
from app.providers.hotels.schemas import (
    HotelProperty,
    HotelSearchOption,
    HotelSearchResult,
    ResolvedHotelSearch,
)
from app.providers.serpapi.client import SerpApiClient

_HTTP_URL = TypeAdapter(HttpUrl)


class SerpApiHotelClient:
    """Map every returned priced property and booking source into app schemas."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: UtcClock = utc_now,
    ) -> None:
        self.client = SerpApiClient(http_client=http_client, settings=settings)
        self.max_results = settings.max_search_results
        self.clock = clock

    async def search_hotels(self, *, search: ResolvedHotelSearch) -> HotelSearchResult:
        request = search.request
        if request.rooms != 1:
            raise UnsupportedHotelRequestError(
                "SerpApi hotel search currently supports one room per search."
            )
        payload = await self.client.search(
            engine="google_hotels",
            params={
                "q": search.location.display_name,
                "check_in_date": request.check_in_date.isoformat(),
                "check_out_date": request.check_out_date.isoformat(),
                "adults": request.adults,
                "children": len(request.children_ages),
                "children_ages": ",".join(map(str, request.children_ages)) or None,
                "free_cancellation": str(request.free_cancellation_only).lower()
                if request.free_cancellation_only
                else None,
                "currency": request.currency,
                "sort_by": 3 if request.max_total_price is not None else 8,
            },
        )
        searched_at = self.clock()
        expiry = searched_at + timedelta(minutes=5)
        rows = [
            ("property", item)
            for item in payload.get("properties", [])
            if isinstance(item, dict)
        ] + [("ad", item) for item in payload.get("ads", []) if isinstance(item, dict)]
        options: list[HotelSearchOption] = []
        for kind, row in rows[: self.max_results]:
            options.extend(
                self._map_options(
                    kind=kind,
                    row=row,
                    search=search,
                    searched_at=searched_at,
                    expires_at=expiry,
                )
            )
        if request.max_total_price is not None:
            options = [
                option
                for option in options
                if option.cheapest_total_price is not None
                and option.cheapest_total_price <= request.max_total_price
            ]

        # Keep one price per hotel so the chat list does not repeat properties.
        cheapest_by_property: dict[str, HotelSearchOption] = {}
        for option in options:
            property_id = option.hotel.accommodation_id
            current = cheapest_by_property.get(property_id)
            if current is None or (
                option.cheapest_total_price is not None
                and (
                    current.cheapest_total_price is None
                    or option.cheapest_total_price < current.cheapest_total_price
                )
            ):
                cheapest_by_property[property_id] = option
        options = list(cheapest_by_property.values())
        options.sort(
            key=lambda option: (
                option.hotel.review_score is None,
                -option.hotel.review_score
                if option.hotel.review_score is not None
                else Decimal(0),
                option.hotel.review_count is None,
                -option.hotel.review_count
                if option.hotel.review_count is not None
                else 0,
                option.cheapest_total_price is None,
                option.cheapest_total_price
                if option.cheapest_total_price is not None
                else Decimal(0),
            )
        )
        options = options[: request.max_results]
        message = None
        if not options:
            message = (
                "No hotel options were found within the stated total-stay budget."
                if request.max_total_price is not None
                else "No hotel options were returned."
            )
        return HotelSearchResult(
            status=(
                HotelSearchStatus.HOTELS_AVAILABLE
                if options
                else HotelSearchStatus.NO_HOTELS
            ),
            searched_at=searched_at,
            location=search.location,
            options=options,
            message=message,
        )

    @classmethod
    def _map_options(
        cls,
        *,
        kind: str,
        row: dict[str, Any],
        search: ResolvedHotelSearch,
        searched_at,
        expires_at,
    ) -> list[HotelSearchOption]:
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            return []
        token = row.get("property_token")
        coordinates = row.get("gps_coordinates") or {}
        property_id = str(token or cls._stable_id(row))
        source_prices = row.get("prices") if kind == "property" else None
        choices: list[tuple[str | None, dict[str, Any], Decimal | None]] = []
        if isinstance(source_prices, list) and source_prices:
            for source_price in source_prices:
                if not isinstance(source_price, dict):
                    continue
                rate = source_price.get("rate_per_night")
                nightly_amount = cls._amount(rate, "extracted_lowest")
                amount = (
                    nightly_amount * search.request.nights
                    if nightly_amount is not None
                    else None
                )
                if amount is not None:
                    choices.append((source_price.get("source"), source_price, amount))
        if not choices:
            total_rate = cls._amount(row.get("total_rate"), "extracted_lowest")
            nightly_rate = cls._amount(row.get("rate_per_night"), "extracted_lowest")
            amount = total_rate
            if amount is None and nightly_rate is not None:
                amount = nightly_rate * search.request.nights
            if amount is None and kind == "ad":
                nightly_rate = cls._decimal(row.get("extracted_price"))
                amount = (
                    nightly_rate * search.request.nights
                    if nightly_rate is not None
                    else None
                )
            choices.append((row.get("source"), row, amount))

        hotel = HotelProperty(
            accommodation_id=property_id,
            name=name[:200],
            description=(
                row.get("description")[:1000]
                if isinstance(row.get("description"), str)
                else None
            ),
            rating=cls._hotel_class(row),
            review_score=cls._decimal(row.get("overall_rating")),
            review_count=cls._integer(row.get("reviews")),
            address=row.get("address") if isinstance(row.get("address"), str) else None,
            city_name=search.location.display_name[:120],
            latitude=cls._coordinate(coordinates.get("latitude"), -90, 90),
            longitude=cls._coordinate(coordinates.get("longitude"), -180, 180),
            amenities=[
                value[:120]
                for value in row.get("amenities", [])
                if isinstance(value, str) and value.strip()
            ][:20]
            if isinstance(row.get("amenities"), list)
            else [],
            photo_urls=cls._photos(row),
        )
        url = cls._safe_url(row.get("link"))
        result: list[HotelSearchOption] = []
        for source, source_row, amount in choices:
            source_name = source if isinstance(source, str) else None
            identity = f"{property_id}|{source_name or 'google'}"
            result.append(
                HotelSearchOption(
                    search_result_id=hashlib.sha256(identity.encode()).hexdigest(),
                    hotel=hotel,
                    check_in_date=search.request.check_in_date,
                    check_out_date=search.request.check_out_date,
                    rooms=search.request.rooms,
                    guest_count=search.request.total_guests,
                    cheapest_total_price=amount,
                    currency=search.request.currency,
                    provider_source=source_name,
                    booking_url=cls._safe_url(source_row.get("link")) or url,
                    expires_at=expires_at.astimezone(UTC),
                    price_is_final=False,
                )
            )
        return result

    @staticmethod
    def _stable_id(row: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode()
        ).hexdigest()

    @staticmethod
    def _amount(value: object, key: str) -> Decimal | None:
        if isinstance(value, dict):
            return SerpApiHotelClient._decimal(value.get(key))
        return None

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return result if result.is_finite() and result >= 0 else None

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result >= 0 else None

    @staticmethod
    def _coordinate(value: object, minimum: int, maximum: int) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if minimum <= result <= maximum else None

    @staticmethod
    def _hotel_class(row: dict[str, Any]) -> int | None:
        value = row.get("extracted_hotel_class", row.get("hotel_class"))
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        return value if isinstance(value, int) and 1 <= value <= 5 else None

    @staticmethod
    def _photos(row: dict[str, Any]) -> list[HttpUrl]:
        image_values: list[object] = []
        thumbnail = row.get("thumbnail")
        if thumbnail:
            image_values.append(thumbnail)
        images = row.get("images")
        if isinstance(images, list):
            image_values.extend(
                image.get("thumbnail") for image in images if isinstance(image, dict)
            )
        urls: list[HttpUrl] = []
        for value in image_values:
            url = SerpApiHotelClient._safe_url(value)
            if url is not None and url not in urls:
                urls.append(url)
            if len(urls) == 3:
                break
        return urls

    @staticmethod
    def _safe_url(value: object) -> HttpUrl | None:
        if not isinstance(value, str):
            return None
        try:
            url = _HTTP_URL.validate_python(value)
        except ValidationError:
            return None
        return url if urlsplit(str(url)).scheme == "https" else None
