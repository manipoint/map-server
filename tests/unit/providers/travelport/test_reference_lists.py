"""Catalog and reference containers reject ambiguous or malformed entries."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.providers.travelport.flight_response_schemas import (
    TravelportCatalogProductOfferings,
    TravelportFlightReferenceList,
    TravelportProductReferenceList,
)


def flight():
    return {
        "id": "f1",
        "carrier": "AA",
        "number": "171",
        "stops": 0,
        "duration": "PT6H11M",
        "Departure": {"location": "JFK", "date": "2027-11-07", "time": "06:30:00"},
        "Arrival": {"location": "LAX", "date": "2027-11-07", "time": "09:41:00"},
    }


def product():
    return {
        "id": "p1",
        "totalDuration": "PT6H11M",
        "FlightSegment": [{"sequence": 1, "Flight": {"FlightRef": "f1"}}],
        "PassengerFlight": [
            {
                "passengerQuantity": 1,
                "passengerTypeCode": "ADT",
                "FlightProduct": [{"segmentSequence": [1], "cabin": "Economy"}],
            }
        ],
    }


def offering():
    return {
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
                            "TotalPrice": "100",
                        },
                    }
                ]
            }
        ],
    }


CONTAINERS = [
    (TravelportFlightReferenceList, "ReferenceListFlight", "Flight", flight),
    (TravelportProductReferenceList, "ReferenceListProduct", "Product", product),
    (
        TravelportCatalogProductOfferings,
        "CatalogProductOfferings",
        "CatalogProductOffering",
        offering,
    ),
]


@pytest.mark.parametrize(("model", "kind", "key", "factory"), CONTAINERS)
def test_valid_container_round_trips(model, kind, key, factory):
    result = model.model_validate({"@type": kind, key: [factory()]})
    assert model.model_validate_json(result.model_dump_json(by_alias=True)) == result


@pytest.mark.parametrize(("model", "kind", "key", "factory"), CONTAINERS)
def test_duplicate_ids_are_rejected(model, kind, key, factory):
    item = factory()
    with pytest.raises(ValidationError, match="unique"):
        model.model_validate({"@type": kind, key: [item, deepcopy(item)]})


@pytest.mark.parametrize(("model", "kind", "key", "factory"), CONTAINERS)
def test_explicit_empty_container_is_valid(model, kind, key, factory):
    model.model_validate({"@type": kind, key: []})


@pytest.mark.parametrize(("model", "kind", "key", "factory"), CONTAINERS)
@pytest.mark.parametrize("value", [None, {}, [None]])
def test_malformed_container_is_rejected(model, kind, key, factory, value):
    with pytest.raises(ValidationError):
        model.model_validate({"@type": kind, key: value})


@pytest.mark.parametrize(("model", "kind", "key", "factory"), CONTAINERS)
def test_missing_collection_is_not_treated_as_empty(model, kind, key, factory):
    with pytest.raises(ValidationError):
        model.model_validate({"@type": kind})


@pytest.mark.parametrize(("model", "kind", "key", "factory"), CONTAINERS[:2])
@pytest.mark.parametrize("kind_value", [None, "WrongType"])
def test_reference_discriminator_is_required_and_checked(
    model, kind, key, factory, kind_value
):
    data = {key: [factory()]}
    if kind_value is not None:
        data["@type"] = kind_value
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_catalog_allows_same_sequence_for_distinct_offers():
    first, second = offering(), offering()
    second["id"] = "o2"
    result = TravelportCatalogProductOfferings.model_validate(
        {"CatalogProductOffering": [first, second]}
    )
    assert len(result.offerings) == 2


def test_catalog_rejects_identical_departure_and_arrival():
    item = offering()
    item["Arrival"] = item["Departure"]
    with pytest.raises(ValidationError, match="different"):
        TravelportCatalogProductOfferings.model_validate(
            {"CatalogProductOffering": [item]}
        )
