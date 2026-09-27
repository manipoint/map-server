"""Travelport response status models."""

from datetime import date, time, timedelta
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.value_objects import CurrencyCode, IataCode


class TravelportResponseMessage(BaseModel):
    """Provider message retained internally, not exposed directly to users."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    message: str | None = Field(
        default=None,
        alias="Message",
        repr=False,
    )


class TravelportResult(BaseModel):
    """Provider errors and non-fatal warnings."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    errors: list[TravelportResponseMessage] = Field(
        default_factory=list,
        alias="Error",
        repr=False,
    )
    warnings: list[TravelportResponseMessage] = Field(
        default_factory=list,
        alias="Warning",
        repr=False,
    )

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


class TravelportCurrencyResponse(BaseModel):
    """Currency attached to a provider-returned price."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    value: CurrencyCode
    decimal_place: int | None = Field(
        default=None,
        alias="decimalPlace",
        ge=0,
        le=9,
    )


class TravelportPriceResponse(BaseModel):
    """Total price and its actual provider-returned currency."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    currency: TravelportCurrencyResponse = Field(
        alias="CurrencyCode",
    )
    total_price: Decimal = Field(
        alias="TotalPrice",
        ge=0,
        allow_inf_nan=False,
    )


class TravelportFlightEndpoint(BaseModel):
    """Airport and local scheduled departure or arrival time."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    location: IataCode
    local_date: date = Field(alias="date")
    local_time: time = Field(alias="time")


class TravelportFlightResponse(BaseModel):
    """Flight detail referenced by a Travelport product."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    id: str = Field(min_length=1, max_length=256)
    carrier: str = Field(
        min_length=2,
        max_length=3,
        pattern=r"^[A-Z0-9]{2,3}$",
    )
    number: str = Field(
        min_length=1,
        max_length=8,
        pattern=r"^[A-Z0-9]{1,8}$",
    )
    duration: timedelta = Field(gt=timedelta(0))
    stops: int = Field(ge=0)

    departure: TravelportFlightEndpoint = Field(alias="Departure")
    arrival: TravelportFlightEndpoint = Field(alias="Arrival")


class TravelportFlightReference(BaseModel):
    """Reference to an entry in ReferenceListFlight."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    flight_ref: str = Field(
        alias="FlightRef",
        min_length=1,
        max_length=256,
    )


class TravelportProductSegment(BaseModel):
    """Position of a referenced flight within a product."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    sequence: int = Field(ge=1)
    flight: TravelportFlightReference = Field(alias="Flight")


class TravelportFlightProductResponse(BaseModel):
    """Cabin and booking class for specified product segments."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    segment_sequences: list[int] = Field(
        alias="segmentSequence",
        min_length=1,
    )
    cabin: str = Field(min_length=1, max_length=64)
    class_of_service: str | None = Field(
        default=None,
        alias="classOfService",
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_segment_sequences(self) -> Self:
        if any(sequence < 1 for sequence in self.segment_sequences):
            raise ValueError("Segment sequences must be positive")

        if len(self.segment_sequences) != len(set(self.segment_sequences)):
            raise ValueError("Segment sequences must be unique")

        return self


class TravelportPassengerFlightResponse(BaseModel):
    """Returned fare details for one passenger category."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    passenger_quantity: int = Field(
        alias="passengerQuantity",
        ge=1,
    )
    passenger_type_code: str = Field(
        alias="passengerTypeCode",
        min_length=1,
        max_length=16,
    )
    flight_products: list[TravelportFlightProductResponse] = Field(
        alias="FlightProduct",
        min_length=1,
    )


class TravelportProductResponse(BaseModel):
    """Journey structure and passenger-specific cabin information."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    id: str = Field(min_length=1, max_length=256)

    total_duration: timedelta = Field(
        alias="totalDuration",
        gt=timedelta(0),
    )

    segments: list[TravelportProductSegment] = Field(
        alias="FlightSegment",
        min_length=1,
    )

    passenger_flights: list[TravelportPassengerFlightResponse] = Field(
        alias="PassengerFlight",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_segment_references(self) -> Self:
        sequences = [segment.sequence for segment in self.segments]
        known_sequences = set(sequences)

        if len(sequences) != len(known_sequences):
            raise ValueError("Product segment sequences must be unique")

        for passenger in self.passenger_flights:
            assigned_sequences: set[int] = set()

            for flight_product in passenger.flight_products:
                references = set(flight_product.segment_sequences)

                if not references.issubset(known_sequences):
                    raise ValueError("Passenger fare references an unknown segment")

                if assigned_sequences.intersection(references):
                    raise ValueError("Passenger fare assigns a segment more than once")

                assigned_sequences.update(references)

            if assigned_sequences != known_sequences:
                raise ValueError("Passenger fare must describe every product segment")

        return self


class TravelportIdentifier(BaseModel):
    """Opaque provider identifier; never decode or construct it locally."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    value: str = Field(min_length=1, repr=False)
    authority: str | None = Field(
        default=None,
        min_length=1,
    )


class TravelportProductReference(BaseModel):
    """Reference to a product in ReferenceListProduct."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    product_ref: str = Field(
        alias="productRef",
        min_length=1,
        max_length=256,
    )


class TravelportProductBrandOffering(BaseModel):
    """One fare option with its product references and quoted price."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    identifier: TravelportIdentifier | None = Field(
        default=None,
        alias="Identifier",
        repr=False,
    )

    products: list[TravelportProductReference] = Field(
        alias="Product",
        min_length=1,
    )

    best_combinable_price: TravelportPriceResponse = Field(
        alias="BestCombinablePrice",
    )

    combinability_codes: list[str] = Field(
        default_factory=list,
        alias="CombinabilityCode",
    )

    content_source: str = Field(
        alias="ContentSource",
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        product_refs = [product.product_ref for product in self.products]

        if len(product_refs) != len(set(product_refs)):
            raise ValueError("Offer product references must be unique")

        if any(not code.strip() for code in self.combinability_codes):
            raise ValueError("Combinability codes must not be blank")

        return self


class TravelportProductBrandOptions(BaseModel):
    """Fare options grouped together by the provider."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    offerings: list[TravelportProductBrandOffering] = Field(
        alias="ProductBrandOffering",
        min_length=1,
    )


class TravelportCatalogOffering(BaseModel):
    """Route-level offering containing its available fare options."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1)

    departure: IataCode = Field(alias="Departure")
    arrival: IataCode = Field(alias="Arrival")

    identifier: TravelportIdentifier | None = Field(
        default=None,
        alias="Identifier",
        repr=False,
    )

    product_brand_options: list[TravelportProductBrandOptions] = Field(
        alias="ProductBrandOptions",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        if self.departure == self.arrival:
            raise ValueError("Catalog departure and arrival must be different")

        return self


class TravelportCatalogProductOfferings(BaseModel):
    """Catalog container; an explicitly empty list contains no offers."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    offerings: list[TravelportCatalogOffering] = Field(
        alias="CatalogProductOffering",
    )

    @model_validator(mode="after")
    def validate_offering_ids(self) -> Self:
        ids = [offering.id for offering in self.offerings]

        if len(ids) != len(set(ids)):
            raise ValueError("Catalog offering IDs must be unique")

        return self


class TravelportFlightReferenceList(BaseModel):
    """Flight definitions referenced by product segments."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    type_: Literal["ReferenceListFlight"] = Field(alias="@type")

    flights: list[TravelportFlightResponse] = Field(alias="Flight")

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        ids = [flight.id for flight in self.flights]

        if len(ids) != len(set(ids)):
            raise ValueError("Flight reference IDs must be unique")

        return self


class TravelportProductReferenceList(BaseModel):
    """Product definitions referenced by catalog fare options."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    type_: Literal["ReferenceListProduct"] = Field(alias="@type")

    products: list[TravelportProductResponse] = Field(alias="Product")

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        ids = [product.id for product in self.products]

        if len(ids) != len(set(ids)):
            raise ValueError("Product reference IDs must be unique")

        return self
