"""Map normalized hotel searches to Duffel Stays payloads."""

from datetime import datetime

from app.domain.hotels import HotelSearchStatus
from app.providers.hotels.duffel_schemas import (
    DuffelGeographicCoordinates,
    DuffelStayAccommodationResponse,
    DuffelStayGuest,
    DuffelStayLocation,
    DuffelStaySearchData,
    DuffelStaySearchPayload,
    DuffelStaySearchResponse,
    DuffelStaySearchResultResponse,
)
from app.providers.hotels.schemas import (
    HotelProperty,
    HotelSearchOption,
    HotelSearchResult,
    ResolvedHotelSearch,
)


def build_duffel_stay_search(
    search: ResolvedHotelSearch,
) -> DuffelStaySearchPayload:
    """Convert one resolved hotel search into a Duffel payload."""

    request = search.request

    guests = [DuffelStayGuest(type="adult") for _ in range(request.adults)]
    guests.extend(
        DuffelStayGuest(type="child", age=age) for age in request.children_ages
    )
    return DuffelStaySearchPayload(
        data=DuffelStaySearchData(
            location=DuffelStayLocation(
                radius=search.radius_km,
                geographic_coordinates=(
                    DuffelGeographicCoordinates(
                        latitude=search.location.latitude,
                        longitude=search.location.longitude,
                    )
                ),
            ),
            check_in_date=request.check_in_date,
            check_out_date=request.check_out_date,
            guests=guests,
            rooms=request.rooms,
            free_cancellation_only=(request.free_cancellation_only),
            mobile=True,
        )
    )


def bounded_optional_text(
    value: str | None,
    *,
    max_length: int,
) -> str | None:
    """Return compact non-blank provider text."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:max_length]


def map_duffel_accommodation(
    accommodation: DuffelStayAccommodationResponse,
) -> HotelProperty:
    """Map one Duffel accommodation to normalized hotel information."""

    location = accommodation.location
    address = location.address
    coordinates = location.geographic_coordinates

    address_parts = [
        address.line_one,
        address.line_two,
        address.city_name,
        address.region,
        address.postal_code,
        address.country_code,
    ]

    display_address = ", ".join(
        part.strip() for part in address_parts if part is not None and part.strip()
    )
    amenities: list[str] = []
    seen_amenities: set[str] = set()
    for amenity in accommodation.amenities or []:
        value = amenity.description or amenity.type
        value = value.strip()[:120]
        key = value.casefold()

        if value and key not in seen_amenities:
            amenities.append(value)
            seen_amenities.add(key)

        if len(amenities) == 20:
            break

    photo_urls: list[str] = []
    seen_photos: set[str] = set()

    for photo in accommodation.photos:
        url = str(photo.url)
        if url not in seen_photos:
            photo_urls.append(url)
            seen_photos.add(url)

        if len(photo_urls) == 3:
            break

    return HotelProperty(
        accommodation_id=accommodation.id,
        name=accommodation.name[:200],
        description=bounded_optional_text(
            accommodation.description,
            max_length=1000,
        ),
        rating=accommodation.rating,
        review_score=accommodation.review_score,
        review_count=accommodation.review_count,
        address=bounded_optional_text(
            display_address,
            max_length=500,
        ),
        city_name=address.city_name,
        country_code=address.country_code,
        latitude=coordinates.latitude,
        longitude=coordinates.longitude,
        amenities=amenities,
        photo_urls=photo_urls,
    )


def map_duffel_stay_result(
    result: DuffelStaySearchResultResponse,
    *,
    search: ResolvedHotelSearch,
) -> HotelSearchOption:
    """Map one Duffel search result to a normalized hotel option."""

    request = search.request

    if result.check_in_date != request.check_in_date:
        raise ValueError("Duffel stay result check-in date does not match the search")

    if result.check_out_date != request.check_out_date:
        raise ValueError("Duffel stay result checkout date does not match the search")

    if result.rooms != request.rooms:
        raise ValueError("Duffel stay result room count does not match the search")

    return HotelSearchOption(
        search_result_id=result.id,
        hotel=map_duffel_accommodation(result.accommodation),
        check_in_date=result.check_in_date,
        check_out_date=result.check_out_date,
        rooms=result.rooms,
        guest_count=request.total_guests,
        cheapest_total_price=result.cheapest_rate_total_amount,
        currency=result.cheapest_rate_currency,
        expires_at=result.expires_at,
    )


def map_duffel_stay_search_response(
    *,
    response: DuffelStaySearchResponse,
    search: ResolvedHotelSearch,
    searched_at: datetime,
) -> HotelSearchResult:
    """Build one bounded normalized result from Duffel hotel options."""

    if searched_at.utcoffset() is None:
        raise ValueError("searched_at must include a timezone")

    current_results = [
        result for result in response.data.results if result.expires_at > searched_at
    ]

    options = [
        map_duffel_stay_result(result, search=search) for result in current_results
    ]

    currencies = {option.currency for option in options}
    if len(currencies) > 1:
        raise ValueError("Duffel stay results contain multiple currencies")

    options.sort(
        key=lambda option: (
            option.cheapest_total_price,
            option.search_result_id,
        )
    )
    selected_options = options[: search.request.max_results]

    if not selected_options:
        return HotelSearchResult(
            status=HotelSearchStatus.NO_HOTELS,
            searched_at=searched_at,
            location=search.location,
            message="No current hotels were found for this search.",
        )

    return HotelSearchResult(
        status=HotelSearchStatus.HOTELS_AVAILABLE,
        searched_at=searched_at,
        location=search.location,
        options=selected_options,
    )
