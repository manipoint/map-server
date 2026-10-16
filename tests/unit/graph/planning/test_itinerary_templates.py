"""Tests for trusted localized itinerary activity text."""

import pytest

from app.graph.planning.itinerary_templates import generic_activity_text


@pytest.mark.parametrize(
    ("code", "expected_title"),
    [
        ("breakfast", "Breakfast"),
        ("lunch", "Lunch"),
        ("dinner", "Dinner"),
        ("hike", "Optional hike"),
        ("photography", "Photography time"),
        ("explore", "Explore the area"),
        ("walk", "Optional walk"),
        ("meal", "Meal break"),
        ("transfer", "Transfer"),
        ("rest", "Rest break"),
        ("free_time", "Free time"),
        ("note", "Planning reminder"),
    ],
)
def test_returns_english_activity_text(
    code: str,
    expected_title: str,
) -> None:
    title, description = generic_activity_text(code, language="en")

    assert title == expected_title
    assert description


@pytest.mark.parametrize(
    ("code", "expected_title"),
    [
        ("breakfast", "Nashta"),
        ("lunch", "Dopehar ka khana"),
        ("dinner", "Raat ka khana"),
        ("hike", "Ikhtiyari hiking"),
        ("photography", "Tasveerain lenay ka waqt"),
        ("explore", "Ilaqa dekhein"),
        ("walk", "Ikhtiyari sair"),
        ("meal", "Khanay ka waqfa"),
        ("transfer", "Safar ka intizam"),
        ("rest", "Aram ka waqfa"),
        ("free_time", "Khali waqt"),
        ("note", "Yad dehani"),
    ],
)
def test_returns_roman_urdu_activity_text(
    code: str,
    expected_title: str,
) -> None:
    title, description = generic_activity_text(code, language="ur-Latn")

    assert title == expected_title
    assert description


@pytest.mark.parametrize("language", ["en", "ur-Latn", "unsupported"])
def test_unknown_activity_uses_free_time_fallback(language: str) -> None:
    actual = generic_activity_text("unknown", language=language)
    expected_language = "ur-Latn" if language == "ur-Latn" else "en"
    expected = generic_activity_text(
        "free_time",
        language=expected_language,
    )

    assert actual == expected


def test_unknown_language_uses_english() -> None:
    assert generic_activity_text(
        "breakfast",
        language="unknown",
    ) == generic_activity_text(
        "breakfast",
        language="en",
    )
