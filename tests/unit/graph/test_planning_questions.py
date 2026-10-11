"""Localized completeness questions stay deterministic, bounded and ordered."""

import pytest

from app.graph.planning_questions import (
    MAX_CLARIFICATION_QUESTIONS,
    QUESTIONS,
    clarification_text,
)
from app.services.trip_requirements_policy import TripRequirementField


def test_questions_cover_every_missing_requirement_code() -> None:
    assert set(QUESTIONS) == set(TripRequirementField)


@pytest.mark.parametrize("field", tuple(TripRequirementField))
@pytest.mark.parametrize(("language", "index"), [("en", 0), ("ur-Latn", 1)])
def test_questions_support_each_field_in_both_languages(
    field: TripRequirementField, language: str, index: int
) -> None:
    question = clarification_text((field,), language)
    assert question == QUESTIONS[field][index]
    assert question.strip() == question
    assert question.endswith("?") or question.endswith(".")


def test_questions_preserve_policy_order_and_missing_field_state() -> None:
    fields = ("destination", "start_date", "end_date", "adults")
    result = clarification_text(fields, "en")
    assert result.splitlines() == [
        "Where would you like to travel?",
        "What is your departure date?",
        "What is your return date or trip duration?",
    ]
    assert len(result.splitlines()) == MAX_CLARIFICATION_QUESTIONS
    assert fields == ("destination", "start_date", "end_date", "adults")


def test_duplicate_fields_do_not_consume_question_slots() -> None:
    fields = ("adults", "adults", "minor_count", "minor_count", "transport")
    assert clarification_text(fields, "en").splitlines() == [
        QUESTIONS["adults"][0],
        QUESTIONS["minor_count"][0],
        QUESTIONS["transport"][0],
    ]


def test_no_missing_fields_produce_no_question() -> None:
    assert clarification_text((), "en") == ""


def test_unsupported_language_retains_english_fallback() -> None:
    assert clarification_text(("destination",), "fr") == (
        "Where would you like to travel?"
    )


def test_unknown_field_is_not_silently_hidden() -> None:
    with pytest.raises(KeyError):
        clarification_text(("unknown_field",), "en")
