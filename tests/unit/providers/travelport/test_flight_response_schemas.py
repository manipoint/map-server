"""Travelport response status contract, including non-fatal warnings."""

import pytest
from pydantic import ValidationError

from app.providers.travelport.flight_response_schemas import (
    TravelportResponseMessage,
    TravelportResult,
)


def test_empty_result_has_no_errors_or_warnings():
    result = TravelportResult.model_validate({"@type": "Result"})
    assert not result.has_errors
    assert not result.has_warnings


def test_real_warning_wire_key_is_preserved_without_failing_search():
    result = TravelportResult.model_validate(
        {
            "@type": "Result",
            "Warning": [
                {"@type": "Warning", "Message": "Fare may change before booking"}
            ],
        }
    )
    assert result.has_warnings
    assert not result.has_errors
    assert result.warnings[0].message == "Fare may change before booking"


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"code": "PROVIDER_ERROR"},
        {"Message": None},
        {"Message": ""},
        {"Message": "Search failed"},
    ],
)
def test_error_entry_is_detected_even_without_message(entry):
    result = TravelportResult.model_validate({"Error": [entry]})
    assert result.has_errors
    assert not result.has_warnings


def test_errors_and_warnings_can_coexist():
    result = TravelportResult.model_validate({"Error": [{}], "Warning": [{}]})
    assert result.has_errors
    assert result.has_warnings


def test_serialization_uses_provider_aliases():
    result = TravelportResult(
        errors=[TravelportResponseMessage(message="error")],
        warnings=[TravelportResponseMessage(message="warning")],
    )
    assert result.model_dump(by_alias=True) == {
        "Error": [{"Message": "error"}],
        "Warning": [{"Message": "warning"}],
    }


@pytest.mark.parametrize("field", ["Error", "Warning"])
@pytest.mark.parametrize("value", [None, {}, "bad", [None], ["bad"]])
def test_malformed_status_collections_are_rejected(field, value):
    with pytest.raises(ValidationError):
        TravelportResult.model_validate({field: value})


def test_provider_details_are_excluded_from_repr():
    message = TravelportResponseMessage(message="private-provider-detail")
    result = TravelportResult(errors=[message], warnings=[message])
    assert "private-provider-detail" not in repr(message)
    assert "private-provider-detail" not in repr(result)


def test_validation_error_text_hides_invalid_message_input():
    with pytest.raises(ValidationError) as caught:
        TravelportResponseMessage.model_validate(
            {"Message": {"private": "sensitive-value"}}
        )
    assert "sensitive-value" not in str(caught.value)


def test_result_default_lists_are_not_shared():
    first = TravelportResult()
    second = TravelportResult()
    first.errors.append(TravelportResponseMessage())
    first.warnings.append(TravelportResponseMessage())
    assert not second.has_errors
    assert not second.has_warnings


def test_unknown_provider_fields_are_tolerated():
    result = TravelportResult.model_validate(
        {"futureField": True, "Error": [{"futureCode": "x"}]}
    )
    assert result.has_errors
