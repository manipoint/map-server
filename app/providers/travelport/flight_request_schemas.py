"""Travelport-specific flight search request models."""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.value_objects import CurrencyCode

TravelportCabin = Literal[
    "Economy",
    "PremiumEconomy",
    "Business",
    "First",
]


class TravelportPassengerCriteria(BaseModel):
    """One passenger category, optionally grouped by age."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["PassengerCriteria"] = Field(
        default="PassengerCriteria",
        alias="@type",
    )
    number: int = Field(ge=1, le=9)
    passenger_type_code: Literal["ADT", "CNN", "INF", "INS"] = Field(
        alias="passengerTypeCode"
    )
    age: int | None = Field(default=None, ge=0, le=17)

    @model_validator(mode="after")
    def validate_passenger_age(self) -> Self:
        if self.passenger_type_code == "ADT":
            if self.age is not None:
                raise ValueError("Adult criteria must not include a child age")
            return self

        if self.age is None:
            raise ValueError("Child and infant criteria require an age")

        if self.passenger_type_code == "CNN" and self.age < 2:
            raise ValueError("Child criteria require an age of at least 2")

        if self.passenger_type_code in {"INF", "INS"} and self.age > 1:
            raise ValueError("Infant criteria require an age of 0 or 1")

        return self


class TravelportLocationCode(BaseModel):
    """Airport or city IATA code used in a search route."""

    model_config = ConfigDict(extra="forbid")
    value: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )


class TravelportSearchCriteriaFlight(BaseModel):
    """One outbound or inbound search direction."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["SearchCriteriaFlight"] = Field(
        default="SearchCriteriaFlight",
        alias="@type",
    )
    departure_date: date = Field(alias="departureDate")
    origin: TravelportLocationCode = Field(alias="From")
    destination: TravelportLocationCode = Field(alias="To")

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        if self.origin.value == self.destination.value:
            raise ValueError("Origin and destination must be different")

        return self


class TravelportCabinPreference(BaseModel):
    """Request the selected cabin across the itinerary."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["CabinPreference"] = Field(
        default="CabinPreference",
        alias="@type",
    )
    preference_type: Literal["Permitted"] = Field(
        default="Permitted",
        alias="preferenceType",
    )
    cabins: list[TravelportCabin] = Field(
        min_length=1,
        max_length=1,
    )


class TravelportFlightType(BaseModel):
    """Restrict results to nonstop flights."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    connection_type: Literal["NonStopDirect"] = Field(
        default="NonStopDirect",
        alias="connectionType",
    )


class TravelportConnectionPreference(BaseModel):
    """Travelport connection preference envelope."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["ConnectionPreferencesAir"] = Field(
        default="ConnectionPreferencesAir",
        alias="@type",
    )
    flight_type: TravelportFlightType = Field(
        alias="FlightType",
    )


class TravelportSearchModifiersAir(BaseModel):
    """Supported cabin and connection search preferences."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["SearchModifiersAir"] = Field(
        default="SearchModifiersAir",
        alias="@type",
    )
    cabin_preferences: list[TravelportCabinPreference] = Field(
        alias="CabinPreference",
        min_length=1,
        max_length=1,
    )
    connection_preferences: list[TravelportConnectionPreference] | None = Field(
        default=None,
        alias="ConnectionPreferences",
        min_length=1,
        max_length=1,
    )


class TravelportPricingModifiersAir(BaseModel):
    """Requested pricing currency."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["PricingModifiersAir"] = Field(
        default="PricingModifiersAir",
        alias="@type",
    )
    currency_code: CurrencyCode = Field(alias="currencyCode")


class TravelportFlightSearchRequest(BaseModel):
    """Supported one-way or round-trip NDC search."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["CatalogProductOfferingsRequestAir"] = Field(
        default="CatalogProductOfferingsRequestAir",
        alias="@type",
    )
    content_source_list: list[Literal["NDC"]] = Field(
        alias="contentSourceList",
        min_length=1,
        max_length=1,
    )
    offers_per_page: int = Field(
        alias="offersPerPage",
        ge=1,
        le=10,
    )
    max_number_of_upsells: Literal[0] = Field(
        default=0,
        alias="maxNumberOfUpsellsToReturn",
    )
    passengers: list[TravelportPassengerCriteria] = Field(
        alias="PassengerCriteria",
        min_length=1,
        max_length=9,
    )
    routes: list[TravelportSearchCriteriaFlight] = Field(
        alias="SearchCriteriaFlight",
        min_length=1,
        max_length=2,
    )
    search_modifiers: TravelportSearchModifiersAir = Field(
        alias="SearchModifiersAir",
    )
    pricing_modifiers: TravelportPricingModifiersAir = Field(
        alias="PricingModifiersAir",
    )

    @model_validator(mode="after")
    def validate_search(self) -> Self:
        adults = sum(
            passenger.number
            for passenger in self.passengers
            if passenger.passenger_type_code == "ADT"
        )
        lap_infants = sum(
            passenger.number
            for passenger in self.passengers
            if passenger.passenger_type_code == "INF"
        )
        total_travelers = sum(passenger.number for passenger in self.passengers)

        if adults < 1:
            raise ValueError("At least one adult is required")

        if lap_infants > adults:
            raise ValueError("Each lap infant must be accompanied by an adult")

        if total_travelers > 9:
            raise ValueError("Travelport search supports at most 9 travelers")

        if len(self.routes) == 2:
            outbound, inbound = self.routes

            if (
                inbound.origin.value != outbound.destination.value
                or inbound.destination.value != outbound.origin.value
            ):
                raise ValueError("Return route must reverse the outbound route")

            if inbound.departure_date < outbound.departure_date:
                raise ValueError("Return date must not precede departure date")

        return self


class TravelportFlightSearchQuery(BaseModel):
    """Top-level body sent to the Travelport search endpoint."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    type_: Literal["CatalogProductOfferingsQueryRequest"] = Field(
        default="CatalogProductOfferingsQueryRequest",
        alias="@type",
    )
    request: TravelportFlightSearchRequest = Field(
        alias="CatalogProductOfferingsRequest",
    )
