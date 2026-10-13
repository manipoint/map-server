from uuid import uuid4

import pytest

from app.domain.city_suggestions import CitySuggestion
from app.domain.destination_selection import (
    CountryMatch,
    PendingDestinationSelection,
)
from app.domain.preferences import TravelInterest
from app.graph.planning_destination_questions import destination_selection_text


def pending(
    *,
    confirmation: bool = False,
    with_options: bool = True,
) -> PendingDestinationSelection:
    return PendingDestinationSelection(
        country=CountryMatch(
            country_code="GB",
            country_name="United Kingdom",
            query="UK",
            requires_confirmation=confirmation,
        ),
        options=(
            (
                CitySuggestion(
                    destination_id=uuid4(),
                    name="London",
                    country_code="GB",
                    matched_interests=(TravelInterest.HISTORY,),
                ),
            )
            if with_options
            else ()
        ),
    )


def test_question_uses_saved_city_and_matching_interest() -> None:
    text = destination_selection_text(pending(), "en")

    assert "United Kingdom" in text
    assert "1. London" in text
    assert "matches your interests: history" in text
    assert "another city" in text
    assert "\\n" not in text
    assert "\n" in text


def test_uncertain_country_requires_confirmation() -> None:
    text = destination_selection_text(pending(confirmation=True), "en")

    assert text.startswith("Did you mean United Kingdom?")


def test_no_catalogue_options_requests_city_input() -> None:
    text = destination_selection_text(pending(with_options=False), "en")

    assert "Please name the city" in text
    assert "No matching destination" not in text


def test_no_interest_match_does_not_claim_personalization() -> None:
    selection = PendingDestinationSelection(
        country=pending().country,
        options=(
            CitySuggestion(
                destination_id=uuid4(),
                name="London",
                country_code="GB",
            ),
        ),
    )

    text = destination_selection_text(selection, "en")

    assert "London" in text
    assert "matches your interests" not in text


@pytest.mark.parametrize("confirmation", [False, True])
def test_roman_urdu_question(confirmation: bool) -> None:
    text = destination_selection_text(
        pending(confirmation=confirmation),
        "ur-Latn",
    )

    assert "United Kingdom" in text
    assert "Shehar ka naam" in text
    if confirmation:
        assert text.startswith("Kya aap ka matlab")
