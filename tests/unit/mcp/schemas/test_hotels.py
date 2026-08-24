"""Tests for hotel-search MCP response schemas."""

import pytest
from pydantic import ValidationError

from app.mcp.schemas.hotels import HotelSearchGuidance


def test_ambiguous_guidance_preserves_bounded_candidates() -> None:
    """Location choices should remain structured for model clarification."""

    guidance = HotelSearchGuidance(
        status="location_ambiguous",
        message="  Please select one location.  ",
        candidates=[
            "London, United Kingdom",
            "London, Ontario, Canada",
        ],
    )

    assert guidance.message == "Please select one location."
    assert guidance.candidates == [
        "London, United Kingdom",
        "London, Ontario, Canada",
    ]


def test_ambiguous_guidance_requires_candidates() -> None:
    """An ambiguity response without choices cannot guide the user."""

    with pytest.raises(ValidationError, match="requires candidates"):
        HotelSearchGuidance(
            status="location_ambiguous",
            message="Please select one location.",
        )


@pytest.mark.parametrize("status", ["location_not_found", "invalid_dates"])
def test_non_ambiguous_guidance_rejects_candidates(status: str) -> None:
    """Candidates should not appear for unrelated guidance outcomes."""

    with pytest.raises(ValidationError, match="only location_ambiguous"):
        HotelSearchGuidance.model_validate(
            {
                "status": status,
                "message": "Update the search.",
                "candidates": ["London, United Kingdom"],
            }
        )


def test_guidance_rejects_unbounded_or_unknown_content() -> None:
    """MCP guidance must stay compact and reject unsupported fields."""

    with pytest.raises(ValidationError):
        HotelSearchGuidance(
            status="location_not_found",
            message=" ",
        )

    with pytest.raises(ValidationError):
        HotelSearchGuidance(
            status="location_ambiguous",
            message="Please select one location.",
            candidates=["x" * 201],
        )

    with pytest.raises(ValidationError):
        HotelSearchGuidance.model_validate(
            {
                "status": "location_not_found",
                "message": "No destination found.",
                "unsupported": True,
            }
        )
