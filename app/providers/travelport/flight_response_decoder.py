"""Decode Travelport search responses and validate reference integrity."""

from dataclasses import dataclass
from typing import TypeVar

from app.common.exceptions import ProviderUnavailableError
from app.providers.travelport.flight_response_schemas import (
    TravelportCatalogProductOfferings,
    TravelportFlightReferenceList,
    TravelportFlightResponse,
    TravelportProductReferenceList,
    TravelportProductResponse,
    TravelportResult,
)

ReferenceItem = TypeVar(
    "ReferenceItem",
    TravelportFlightResponse,
    TravelportProductResponse,
)


@dataclass
class DecodedTravelportSearch:
    """Validated provider data ready for normalized offer mapping."""

    catalog: TravelportCatalogProductOfferings
    flights_by_id: dict[str, TravelportFlightResponse]
    products_by_id: dict[str, TravelportProductResponse]
    result: TravelportResult


def _add_unique_references(
    target: dict[str, ReferenceItem], items: list[ReferenceItem]
) -> None:
    """Reject duplicates across separate reference-list sections."""

    for item in items:
        if item.id in target:
            raise ValueError("Duplicate reference ID")
        target[item.id] = item


def decode_travelport_search(payload: object) -> DecodedTravelportSearch:
    """Decode a JSON object without inventing missing response data."""

    try:
        if not isinstance(payload, dict):
            raise ValueError("Expected response object")
        response = payload.get("CatalogProductOfferingsResponse")
        if not isinstance(response, dict):
            raise ValueError("Missing search response envelope")

        result = TravelportResult.model_validate(response.get("Result", {}))

        if result.has_errors:
            raise ProviderUnavailableError("Travelport flight search returned an error")

        catalog = TravelportCatalogProductOfferings.model_validate(
            response.get("CatalogProductOfferings")
        )
        references = response.get("ReferenceList", [])
        if not isinstance(references, list):
            raise ValueError("Invalid reference list")
        flights_by_id: dict[str, TravelportFlightResponse] = {}
        products_by_id: dict[str, TravelportProductResponse] = {}

        for section in references:
            if not isinstance(section, dict):
                raise ValueError("Invalid reference section")
            kind = section.get("@type")
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError("Missing reference discriminator")
            if kind == "ReferenceListFlight":
                parsed_flights = TravelportFlightReferenceList.model_validate(section)

                _add_unique_references(flights_by_id, parsed_flights.flights)

            elif kind == "ReferenceListProduct":
                parsed_products = TravelportProductReferenceList.model_validate(section)
                _add_unique_references(
                    products_by_id,
                    parsed_products.products,
                )

        for product in products_by_id.values():
            for segment in product.segments:
                if segment.flight.flight_ref not in flights_by_id:
                    raise ValueError("Unresolved flight reference")

        for offering in catalog.offerings:
            for option in offering.product_brand_options:
                for fare in option.offerings:
                    for product_reference in fare.products:
                        if product_reference.product_ref not in products_by_id:
                            raise ValueError("Unresolved product reference")

        return DecodedTravelportSearch(
            catalog=catalog,
            flights_by_id=flights_by_id,
            products_by_id=products_by_id,
            result=result,
        )
    except (ValueError, TypeError, KeyError):
        raise ProviderUnavailableError(
            "Travelport returned an invalid flight search response"
        ) from None
