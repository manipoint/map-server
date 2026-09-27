"""Decoder validates reference integrity and sanitizes provider failures."""

import traceback
from copy import deepcopy

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.providers.travelport.flight_response_decoder import decode_travelport_search


def payload():
    return {
        "CatalogProductOfferingsResponse": {
            "Result": {"Warning": [{"Message": "Price may change"}]},
            "CatalogProductOfferings": {
                "CatalogProductOffering": [
                    {
                        "id": "o1",
                        "sequence": 1,
                        "Departure": "JFK",
                        "Arrival": "LAX",
                        "ProductBrandOptions": [
                            {
                                "ProductBrandOffering": [
                                    {
                                        "Product": [{"productRef": "p1"}],
                                        "ContentSource": "NDC",
                                        "BestCombinablePrice": {
                                            "CurrencyCode": {"value": "USD"},
                                            "TotalPrice": "100.25",
                                        },
                                    }
                                ]
                            }
                        ],
                    }
                ]
            },
            "ReferenceList": [
                {
                    "@type": "ReferenceListFlight",
                    "Flight": [
                        {
                            "id": "f1",
                            "carrier": "AA",
                            "number": "171",
                            "stops": 0,
                            "duration": "PT6H11M",
                            "Departure": {
                                "location": "JFK",
                                "date": "2027-11-07",
                                "time": "06:30:00",
                            },
                            "Arrival": {
                                "location": "LAX",
                                "date": "2027-11-07",
                                "time": "09:41:00",
                            },
                        }
                    ],
                },
                {
                    "@type": "ReferenceListProduct",
                    "Product": [
                        {
                            "id": "p1",
                            "totalDuration": "PT6H11M",
                            "FlightSegment": [
                                {"sequence": 1, "Flight": {"FlightRef": "f1"}}
                            ],
                            "PassengerFlight": [
                                {
                                    "passengerQuantity": 1,
                                    "passengerTypeCode": "ADT",
                                    "FlightProduct": [
                                        {"segmentSequence": [1], "cabin": "Economy"}
                                    ],
                                }
                            ],
                        }
                    ],
                },
            ],
        }
    }


def test_decodes_links_and_preserves_warning_without_mutating_payload():
    data = payload()
    before = deepcopy(data)
    result = decode_travelport_search(data)
    fare = result.catalog.offerings[0].product_brand_options[0].offerings[0]
    product = result.products_by_id[fare.products[0].product_ref]
    assert result.flights_by_id[product.segments[0].flight.flight_ref].carrier == "AA"
    assert result.result.has_warnings
    assert not result.result.has_errors
    assert data == before


def test_reference_order_and_unused_types_do_not_affect_decoding():
    data = payload()
    refs = data["CatalogProductOfferingsResponse"]["ReferenceList"]
    refs.reverse()
    refs.append({"@type": "ReferenceListBrand", "Brand": []})
    assert len(decode_travelport_search(data).flights_by_id) == 1


def test_explicit_empty_catalog_can_omit_references():
    result = decode_travelport_search(
        {
            "CatalogProductOfferingsResponse": {
                "CatalogProductOfferings": {"CatalogProductOffering": []},
            }
        }
    )
    assert result.catalog.offerings == []
    assert result.flights_by_id == result.products_by_id == {}


def test_provider_error_is_checked_before_success_fields():
    with pytest.raises(ProviderUnavailableError, match="returned an error"):
        decode_travelport_search(
            {
                "CatalogProductOfferingsResponse": {
                    "Result": {"Error": [{"Message": "private-provider-detail"}]},
                }
            }
        )


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"CatalogProductOfferingsResponse": None},
        {"CatalogProductOfferingsResponse": {}},
    ],
)
def test_invalid_envelope_is_not_no_availability(data):
    with pytest.raises(ProviderUnavailableError):
        decode_travelport_search(data)


@pytest.mark.parametrize(
    "references",
    [None, {}, [None], [{}], [{"@type": " "}], [{"@type": "ReferenceListFlight"}]],
)
def test_invalid_reference_sections_are_rejected(references):
    data = payload()
    data["CatalogProductOfferingsResponse"]["ReferenceList"] = references
    with pytest.raises(ProviderUnavailableError):
        decode_travelport_search(data)


@pytest.mark.parametrize("index", [0, 1])
def test_duplicate_ids_across_sections_are_rejected(index):
    data = payload()
    refs = data["CatalogProductOfferingsResponse"]["ReferenceList"]
    refs.append(deepcopy(refs[index]))
    with pytest.raises(ProviderUnavailableError):
        decode_travelport_search(data)


@pytest.mark.parametrize("index", [0, 1])
def test_missing_flight_or_product_definitions_are_rejected(index):
    data = payload()
    del data["CatalogProductOfferingsResponse"]["ReferenceList"][index]
    with pytest.raises(ProviderUnavailableError):
        decode_travelport_search(data)


def test_validation_traceback_does_not_expose_provider_input():
    data = payload()
    flight = data["CatalogProductOfferingsResponse"]["ReferenceList"][0]["Flight"][0]
    flight["carrier"] = "private-provider-detail"
    with pytest.raises(ProviderUnavailableError) as caught:
        decode_travelport_search(data)
    assert caught.value.__suppress_context__
    assert "private-provider-detail" not in "".join(
        traceback.format_exception(caught.value)
    )
