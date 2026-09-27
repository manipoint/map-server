"""Travelport token response validation."""

import pytest
from pydantic import ValidationError

from app.providers.travelport.auth_schemas import TravelportTokenResponse


def test_minimal_response_uses_documented_lifetime_and_hides_token():
    result = TravelportTokenResponse.model_validate(
        {"access_token": "private-token", "extra": True}
    )
    assert result.expires_in == 86_400
    assert result.token_type == "Bearer"
    assert "private-token" not in repr(result)


@pytest.mark.parametrize("token", ["", " ", "abc def", "abc\n", None, 123])
def test_invalid_tokens_are_rejected(token):
    with pytest.raises(ValidationError):
        TravelportTokenResponse.model_validate({"access_token": token})


@pytest.mark.parametrize(
    "fields", [{"expires_in": 0}, {"expires_in": -1}, {"token_type": "Basic"}]
)
def test_invalid_expiry_or_token_type_is_rejected(fields):
    with pytest.raises(ValidationError):
        TravelportTokenResponse.model_validate({"access_token": "test-token", **fields})
