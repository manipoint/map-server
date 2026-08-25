"""Tests for place-search MCP response schemas."""

import pytest
from pydantic import ValidationError

from app.mcp.schemas.places import PlaceSearchGuidance


def test_ambiguous_guidance_preserves_bounded_candidates() -> None:
    """Location choices should remain structured for model clarification."""

    guidance = PlaceSearchGuidance(
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
        PlaceSearchGuidance(
            status="location_ambiguous",
            message="Please select one location.",
        )


def test_not_found_guidance_rejects_candidates() -> None:
    """Candidates should not accompany a location-not-found outcome."""

    with pytest.raises(ValidationError, match="only location_ambiguous"):
        PlaceSearchGuidance(
            status="location_not_found",
            message="No destination found.",
            candidates=["London, United Kingdom"],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "location_not_found",
            "message": " ",
        },
        {
            "status": "location_ambiguous",
            "message": "Please select one location.",
            "candidates": ["x" * 201],
        },
        {
            "status": "location_not_found",
            "message": "No destination found.",
            "unsupported": True,
        },
    ],
)
def test_guidance_rejects_invalid_or_unknown_content(
    payload: dict[str, object],
) -> None:
    """MCP guidance should stay compact and reject unsupported content."""

    with pytest.raises(ValidationError):
        PlaceSearchGuidance.model_validate(payload)
