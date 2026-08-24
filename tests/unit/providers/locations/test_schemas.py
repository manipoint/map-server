"""Tests for provider-independent resolved-location schemas."""

import pytest
from pydantic import ValidationError

from app.providers.locations.schemas import ResolvedLocation


def test_resolved_location_normalizes_provider_text() -> None:
    """Resolved location text should be compact while retaining coordinates."""

    location = ResolvedLocation(
        query="  London  ",
        display_name="  London, United Kingdom  ",
        latitude=51.5071,
        longitude=-0.1276,
    )

    assert location.query == "London"
    assert location.display_name == "London, United Kingdom"
    assert location.latitude == 51.5071
    assert location.longitude == -0.1276


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-90.1, 0.0),
        (90.1, 0.0),
        (0.0, -180.1),
        (0.0, 180.1),
    ],
)
def test_resolved_location_rejects_invalid_coordinates(
    latitude: float,
    longitude: float,
) -> None:
    """Invalid provider coordinates must never enter travel searches."""

    with pytest.raises(ValidationError):
        ResolvedLocation(
            query="London",
            display_name="London, United Kingdom",
            latitude=latitude,
            longitude=longitude,
        )


def test_resolved_location_rejects_blank_and_unknown_fields() -> None:
    """Incomplete or unexpected geocoder data should fail validation."""

    with pytest.raises(ValidationError):
        ResolvedLocation(
            query="  ",
            display_name="London, United Kingdom",
            latitude=51.5071,
            longitude=-0.1276,
        )

    with pytest.raises(ValidationError):
        ResolvedLocation.model_validate(
            {
                "query": "London",
                "display_name": "London, United Kingdom",
                "latitude": 51.5071,
                "longitude": -0.1276,
                "unsupported": True,
            }
        )
