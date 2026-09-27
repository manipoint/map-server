"""Offer price and product reference contracts."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.providers.travelport.flight_response_schemas import (
    TravelportProductBrandOffering,
    TravelportProductBrandOptions,
)


def payload():
    return {
        "Identifier": {"authority": "AA", "value": "opaque-test-identifier"},
        "Product": [{"productRef": "p1"}],
        "BestCombinablePrice": {
            "CurrencyCode": {"value": "INR", "decimalPlace": 0},
            "TotalPrice": "24322.123456789",
        },
        "CombinabilityCode": ["AA__CC01"],
        "ContentSource": "NDC",
    }


def test_offer_preserves_price_currency_references_and_opaque_identifier():
    result = TravelportProductBrandOffering.model_validate(payload())
    assert result.best_combinable_price.total_price == Decimal("24322.123456789")
    assert result.best_combinable_price.currency.value == "INR"
    assert result.products[0].product_ref == "p1"
    assert result.combinability_codes == ["AA__CC01"]
    assert result.identifier.value == "opaque-test-identifier"
    assert "opaque-test-identifier" not in repr(result)
    assert "opaque-test-identifier" not in repr(result.identifier)
    assert (
        TravelportProductBrandOffering.model_validate_json(
            result.model_dump_json(by_alias=True)
        )
        == result
    )


def test_missing_optional_metadata_stays_missing():
    data = payload()
    del data["Identifier"]
    del data["CombinabilityCode"]
    result = TravelportProductBrandOffering.model_validate(data)
    assert result.identifier is None
    assert result.combinability_codes == []


@pytest.mark.parametrize(
    "products",
    [[], [{"productRef": "p1"}, {"productRef": "p1"}], [{"productRef": ""}], [{}]],
)
def test_invalid_product_references_are_rejected(products):
    data = payload()
    data["Product"] = products
    with pytest.raises(ValidationError):
        TravelportProductBrandOffering.model_validate(data)


@pytest.mark.parametrize("codes", [[""], ["   "], [None], "AA__CC01", None])
def test_invalid_combination_metadata_is_rejected(codes):
    data = payload()
    data["CombinabilityCode"] = codes
    with pytest.raises(ValidationError):
        TravelportProductBrandOffering.model_validate(data)


@pytest.mark.parametrize("field", ["Product", "BestCombinablePrice", "ContentSource"])
def test_missing_required_offer_data_is_rejected(field):
    data = payload()
    del data[field]
    with pytest.raises(ValidationError):
        TravelportProductBrandOffering.model_validate(data)


def test_multiple_fares_and_multiple_product_references_are_preserved():
    first = payload()
    first["Product"].append({"productRef": "p2"})
    result = TravelportProductBrandOptions.model_validate(
        {
            "@type": "ProductBrandOptions",
            "ProductBrandOffering": [first, payload()],
        }
    )
    assert len(result.offerings) == 2
    assert [p.product_ref for p in result.offerings[0].products] == ["p1", "p2"]


@pytest.mark.parametrize(
    "data", [{}, {"ProductBrandOffering": []}, {"ProductBrandOffering": None}]
)
def test_empty_or_missing_fare_options_are_rejected(data):
    with pytest.raises(ValidationError):
        TravelportProductBrandOptions.model_validate(data)
