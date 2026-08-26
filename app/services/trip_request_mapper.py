"""Map normalized trip requests into bounded provider search inputs."""

from app.domain.trips import TravelerParty, TripRequest
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.flights.schemas import FlightSearchInput
from app.providers.hotels.schemas import HotelSearchInput
from app.providers.places.schemas import PlaceSearchInput


class TripRequestMapper:
    """Create provider-independent search inputs from one trip request."""

    @staticmethod
    def to_flight_search(
        request: TripRequest,
        *,
        origin_airport_code: str,
        destination_airport_code: str,
        max_results: int = 3,
    ) -> FlightSearchInput:
        """Create a round-trip flight-search request.

        Airport codes must already be resolved. This mapper never asks an
        LLM to guess an airport from a city name.
        """

        if request.origin is None:
            raise ValueError("trip origin is required before creating a flight search")

        travelers = request.travelers

        return FlightSearchInput(
            origin=origin_airport_code,
            destination=destination_airport_code,
            departure_date=request.start_date,
            return_date=request.end_date,
            adults=travelers.adults,
            children_ages=travelers.children_ages,
            infants_with_seat_ages=travelers.infants_with_seat_ages,
            infants_on_lap_ages=travelers.infants_on_lap_ages,
            cabin_class=request.cabin_class,
            nonstop_only=request.nonstop_only,
            currency=request.budget_currency,
            max_results=max_results,
        )

    @staticmethod
    def to_hotel_search(
        request: TripRequest,
        *,
        max_results: int = 3,
    ) -> HotelSearchInput:
        """Create a hotel search containing every overnight guest."""

        travelers = request.travelers

        return HotelSearchInput(
            destination=request.destination,
            check_in_date=request.start_date,
            check_out_date=request.end_date,
            adults=travelers.adults,
            children_ages=TripRequestMapper._hotel_minor_ages(travelers),
            rooms=request.rooms,
            free_cancellation_only=request.free_cancellation_only,
            max_results=max_results,
        )

    @staticmethod
    def to_place_search(
        request: TripRequest,
        *,
        max_results: int = 3,
    ) -> PlaceSearchInput:
        """Create a bounded place-discovery request."""
        return PlaceSearchInput(
            destination=request.destination,
            interests=request.interests,
            family_friendly=TripRequestMapper._family_friendly_preference(request),
            max_results=max_results,
        )

    @staticmethod
    def to_weather_search(
        request: TripRequest,
    ) -> CurrentWeatherInput:
        """Create a current-weather request for the destination."""

        return CurrentWeatherInput(city=request.destination)

    @staticmethod
    def _hotel_minor_ages(
        travelers: TravelerParty,
    ) -> list[int]:
        """Return all non-adult ages using hotel guest categories."""
        return [
            *travelers.children_ages,
            *travelers.infants_with_seat_ages,
            *travelers.infants_on_lap_ages,
        ]

    @staticmethod
    def _family_friendly_preference(request: TripRequest) -> bool | None:
        """Infer family-friendly discovery only from explicit trip evidence."""

        travelers = request.travelers
        has_minor_travelers = bool(
            travelers.children_ages
            or travelers.infants_with_seat_ages
            or travelers.infants_on_lap_ages
        )
        requests_family_places = any(
            "family" in interest.casefold() for interest in request.interests
        )
        if has_minor_travelers or requests_family_places:
            return True
        return None
