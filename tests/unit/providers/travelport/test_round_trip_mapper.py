"""Round-trip pairing and whole-journey pricing contracts; no live providers."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.providers.flights.metadata_schemas import FlightMetadata
from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.flight_response_decoder import decode_travelport_search
from app.providers.travelport.round_trip_mapper import map_travelport_round_trip_result

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "travelport"


def payload():
    return json.loads(
        (FIXTURES / "round_trip_response.json").read_text(), parse_float=Decimal
    )


def catalogs(data):
    return data["CatalogProductOfferingsResponse"]["CatalogProductOfferings"][
        "CatalogProductOffering"
    ]


def fare(data, leg):
    return catalogs(data)[leg]["ProductBrandOptions"][0]["ProductBrandOffering"][0]


def map_result(data, search_id=None, **overrides):
    values = dict(
        origin="JFK",
        destination="LAX",
        departure_date="2027-11-08",
        return_date="2027-11-15",
    )
    values.update(overrides)
    return map_travelport_round_trip_result(
        request=FlightSearchInput(**values),
        decoded=decode_travelport_search(data),
        metadata=FlightMetadata.model_validate_json(
            (FIXTURES / "flight_metadata.json").read_text()
        ),
        search_id=search_id if search_id is not None else UUID(int=1),
        searched_at=datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_combined_price_is_counted_once_and_ids_are_search_scoped():
    data = payload()
    result = map_result(data)
    (offer,) = result.offers
    assert offer.total_price == Decimal("486.44")
    assert offer.return_itinerary.segments[-1].arrival_airport == "JFK"
    assert offer.offer_id == map_result(data).offers[0].offer_id
    assert offer.offer_id != map_result(data, search_id=UUID(int=2)).offers[0].offer_id
    assert data == payload()


@pytest.mark.parametrize(
    "change", ["code", "source", "missing_leg", "date", "cabin", "stops"]
)
def test_nonmatching_legs_never_form_an_offer(change):
    data = payload()
    root = data["CatalogProductOfferingsResponse"]
    if change == "code":
        fare(data, 1)["CombinabilityCode"] = ["j2"]
    elif change == "source":
        fare(data, 1)["ContentSource"] = "GDS"
    elif change == "missing_leg":
        catalogs(data).pop()
    elif change == "date":
        flight = root["ReferenceList"][0]["Flight"][1]
        flight["Departure"]["date"] = flight["Arrival"]["date"] = "2027-11-16"
    elif change == "cabin":
        root["ReferenceList"][1]["Product"][1]["PassengerFlight"][0]["FlightProduct"][
            0
        ]["cabin"] = "Business"
    else:
        root["ReferenceList"][0]["Flight"][1]["stops"] = 1
    assert map_result(data, nonstop_only=True).offers == []


@pytest.mark.parametrize(
    "change",
    [
        "price",
        "currency",
        "missing_codes",
        "sequence",
        "route",
        "travelers",
        "multiple_products",
    ],
)
def test_unsafe_provider_data_fails_closed(change):
    data = payload()
    back = fare(data, 1)
    if change == "price":
        back["BestCombinablePrice"]["TotalPrice"] = 1
    elif change == "currency":
        back["BestCombinablePrice"]["CurrencyCode"]["value"] = "EUR"
    elif change == "missing_codes":
        back.pop("CombinabilityCode")
    elif change == "sequence":
        catalogs(data)[1]["sequence"] = 3
    elif change == "route":
        catalogs(data)[1]["Arrival"] = "LHR"
    elif change == "travelers":
        data["CatalogProductOfferingsResponse"]["ReferenceList"][1]["Product"][1][
            "PassengerFlight"
        ][0]["passengerQuantity"] = 2
    else:
        back["Product"].append({"productRef": "fixture-product"})
    with pytest.raises(ProviderUnavailableError):
        map_result(data)


def test_duplicate_codes_do_not_duplicate_results():
    data = payload()
    for leg in range(2):
        fare(data, leg)["CombinabilityCode"] = ["j1", "j1", "j2"]
    assert len(map_result(data).offers) == 1


def test_empty_inventory():
    data = payload()
    catalogs(data).clear()
    assert map_result(data).status.value == "no_offers"


def test_cheapest_combinations_selected_after_pairing():
    data = payload()
    for leg in range(2):
        options = catalogs(data)[leg]["ProductBrandOptions"][0]["ProductBrandOffering"]
        for i, price in enumerate(["100.10", "200.20", "300.30"], start=2):
            item = deepcopy(options[0])
            item["CombinabilityCode"] = [f"j{i}"]
            item["BestCombinablePrice"]["TotalPrice"] = price
            options.append(item)
    result = map_result(data, max_results=2)
    assert [o.total_price for o in result.offers] == [
        Decimal("100.10"),
        Decimal("200.20"),
    ]
    assert len({o.offer_id for o in result.offers}) == 2


def test_return_must_depart_after_outbound_arrives():
    data = payload()
    back = data["CatalogProductOfferingsResponse"]["ReferenceList"][0]["Flight"][1]
    back["Departure"].update(date="2027-11-08", time="08:00:00")
    back["Arrival"].update(date="2027-11-08", time="16:00:00")
    assert map_result(data, return_date="2027-11-08").offers == []


def test_shared_group_expands_both_legs_but_respects_result_limit():
    data = payload()
    for leg in range(2):
        options = catalogs(data)[leg]["ProductBrandOptions"][0]["ProductBrandOffering"]
        options.append(deepcopy(options[0]))
    result = map_result(data, max_results=10)
    assert len(result.offers) == 4
    assert len({offer.offer_id for offer in result.offers}) == 4
    assert all(offer.total_price == Decimal("486.44") for offer in result.offers)
    assert len(map_result(data, max_results=2).offers) == 2
