import pycountry
import pytest

from app.services.country_recognition_service import (
    CountryRecognitionService,
)


@pytest.fixture(scope="module")
def service() -> CountryRecognitionService:
    return CountryRecognitionService()


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("UK", "GB"),
        ("U.K.", "GB"),
        ("Japan", "JP"),
        ("Brazil", "BR"),
        ("New Zealand", "NZ"),
        ("South Africa", "ZA"),
        ("Türkiye", "TR"),
        ("Turkey", "TR"),
        ("Côte d'Ivoire", "CI"),
        ("DE", "DE"),
        ("DEU", "DE"),
    ],
)
def test_exact_global_lookup(
    service: CountryRecognitionService,
    query: str,
    code: str,
) -> None:
    result = service.recognize(query)

    assert result.status == "exact"
    assert result.matches[0].country_code == code
    assert result.matches[0].requires_confirmation is False


@pytest.mark.parametrize("query", ["turky", "Japen", "Japna"])
def test_typo_requires_confirmation(
    service: CountryRecognitionService,
    query: str,
) -> None:
    result = service.recognize(query, allow_typo=True)

    assert result.status in {"confirmation_required", "ambiguous"}
    expected = "TR" if query == "turky" else "JP"
    assert expected in {match.country_code for match in result.matches}
    assert all(match.requires_confirmation for match in result.matches)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Congo", {"CG", "CD"}),
        ("Korea", {"KR", "KP"}),
    ],
)
def test_ambiguous_names_are_not_silently_selected(
    service: CountryRecognitionService,
    query: str,
    expected: set[str],
) -> None:
    result = service.recognize(query)

    assert result.status == "ambiguous"
    assert {match.country_code for match in result.matches} == expected
    assert all(match.requires_confirmation for match in result.matches)


def test_typo_matching_is_disabled_by_default(
    service: CountryRecognitionService,
) -> None:
    assert service.recognize("turky").status == "not_recognized"


def test_city_input_is_not_fuzzy_matched_by_default(
    service: CountryRecognitionService,
) -> None:
    assert service.recognize("Bali").status == "not_recognized"


@pytest.mark.parametrize("query", [None, "", "x", "x" * 121])
def test_invalid_inputs_are_bounded(
    service: CountryRecognitionService,
    query: str | None,
) -> None:
    result = service.recognize(query, allow_typo=True)

    assert result.status == "invalid_input"
    assert result.matches == ()


def test_unknown_input_remains_unrecognized(
    service: CountryRecognitionService,
) -> None:
    result = service.recognize("zzzzzzzzzz", allow_typo=True)

    assert result.status == "not_recognized"
    assert result.matches == ()


def test_every_dataset_country_supports_exact_iso_code_lookup(
    service: CountryRecognitionService,
) -> None:
    for record in pycountry.countries:
        for query in (record.alpha_2, record.alpha_3):
            result = service.recognize(query)
            assert result.status == "exact", query
            assert len(result.matches) == 1
            assert result.matches[0].country_code == record.alpha_2
            assert result.matches[0].requires_confirmation is False


@pytest.mark.parametrize("query", ["  jApAn  ", "United   Kingdom", "Cote d'Ivoire"])
def test_normalization_preserves_trimmed_original_query(
    service: CountryRecognitionService, query: str
) -> None:
    result = service.recognize(query)
    assert result.status == "exact"
    assert result.matches[0].query == query.strip()


@pytest.mark.parametrize("query", ["@@", "\u0301\u0301", "   "])
def test_inputs_without_normalized_characters_are_invalid(
    service: CountryRecognitionService, query: str
) -> None:
    result = service.recognize(query, allow_typo=True)
    assert result.status == "invalid_input"
    assert result.matches == ()


def test_short_unrecognized_abbreviations_are_not_fuzzy_matched(
    service: CountryRecognitionService,
) -> None:
    result = service.recognize("zz", allow_typo=True)
    assert result.status == "not_recognized"
    assert result.matches == ()


def test_fuzzy_proposals_are_bounded_unique_and_repeatable(
    service: CountryRecognitionService,
) -> None:
    first = service.recognize("turky", allow_typo=True)
    second = service.recognize("turky", allow_typo=True)
    assert first == second
    assert 1 <= len(first.matches) <= 3
    assert len({match.country_code for match in first.matches}) == len(first.matches)
    assert all(match.requires_confirmation for match in first.matches)


def test_enabling_typo_matching_does_not_override_an_exact_country(
    service: CountryRecognitionService,
) -> None:
    assert service.recognize("Japan", allow_typo=True) == service.recognize("Japan")
