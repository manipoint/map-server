"""Validation regressions for country recognition and pending city choices."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.city_suggestions import CitySuggestion
from app.domain.destination_selection import (
    CountryMatch,
    CountryRecognition,
    PendingDestinationSelection,
)
from app.domain.planning import PlanningState


def country(code: str = "GB", *, confirmation: bool = False) -> CountryMatch:
    return CountryMatch(
        country_code=code,
        country_name="United Kingdom" if code == "GB" else "Japan",
        query="UK",
        requires_confirmation=confirmation,
    )


def test_ambiguous_country_proposals_survive_owned_planning_state_json():
    proposals = CountryRecognition(
        status="ambiguous",
        matches=(country(confirmation=True), country("JP", confirmation=True)),
    )
    state = PlanningState(pending_country_selection=proposals)
    restored = PlanningState.model_validate_json(state.model_dump_json())
    assert restored.pending_country_selection == proposals


def test_old_planning_state_defaults_to_no_pending_country():
    payload = PlanningState().model_dump(mode="json")
    payload.pop("pending_country_selection")
    assert PlanningState.model_validate(payload).pending_country_selection is None


@pytest.mark.parametrize(
    ("status", "matches"),
    [
        ("exact", (country(),)),
        ("confirmation_required", (country(confirmation=True),)),
        (
            "ambiguous",
            (country(confirmation=True), country("JP", confirmation=True)),
        ),
        ("not_recognized", ()),
        ("invalid_input", ()),
    ],
)
def test_valid_recognition_returns_model_and_survives_json(status, matches) -> None:
    direct = CountryRecognition(status=status, matches=matches)
    validated = CountryRecognition.model_validate(
        {"status": status, "matches": matches}
    )

    # Detect an after-validator that accidentally falls through without self.
    assert isinstance(validated, CountryRecognition)
    assert validated == direct
    assert CountryRecognition.model_validate_json(direct.model_dump_json()) == direct


@pytest.mark.parametrize(
    ("status", "matches", "error"),
    [
        ("exact", (), "exactly one match"),
        ("exact", (country(), country("JP")), "exactly one match"),
        ("confirmation_required", (), "exactly one match"),
        (
            "confirmation_required",
            (country(confirmation=True), country("JP", confirmation=True)),
            "exactly one match",
        ),
        ("ambiguous", (), "multiple matches"),
        ("ambiguous", (country(confirmation=True),), "multiple matches"),
        (
            "ambiguous",
            (country(confirmation=True), country(confirmation=True)),
            "unique codes",
        ),
        ("exact", (country(confirmation=True),), "Confirmation flags"),
        ("confirmation_required", (country(),), "Confirmation flags"),
        (
            "ambiguous",
            (country(), country("JP", confirmation=True)),
            "Confirmation flags",
        ),
        ("not_recognized", (country(),), "cannot contain matches"),
        ("invalid_input", (country(),), "cannot contain matches"),
    ],
)
@pytest.mark.parametrize("use_model_validate", [False, True])
def test_invalid_recognition_is_rejected(
    status, matches, error: str, use_model_validate: bool
) -> None:
    with pytest.raises(ValidationError, match=error):
        if use_model_validate:
            CountryRecognition.model_validate({"status": status, "matches": matches})
        else:
            CountryRecognition(status=status, matches=matches)


def test_recognition_rejects_more_than_three_matches() -> None:
    with pytest.raises(ValidationError):
        CountryRecognition(
            status="ambiguous",
            matches=tuple(
                country(code, confirmation=True) for code in ("GB", "JP", "TR", "DE")
            ),
        )


def test_recognition_rejects_unexpected_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        CountryRecognition.model_validate(
            {"status": "not_recognized", "selected": True}
        )


def test_country_normalizes_codes_and_rejects_invalid_code_shape() -> None:
    assert country(" gb ").country_code == "GB"
    with pytest.raises(ValidationError):
        country("GBR")


def test_pending_selection_allows_no_catalogue_options() -> None:
    pending = PendingDestinationSelection(country=country())
    assert pending.options == ()


def test_pending_selection_rejects_duplicate_ids() -> None:
    option = CitySuggestion(destination_id=uuid4(), name="London", country_code="GB")
    with pytest.raises(ValidationError, match="unique IDs"):
        PendingDestinationSelection(country=country(), options=(option, option))


def test_pending_selection_rejects_another_country() -> None:
    option = CitySuggestion(destination_id=uuid4(), name="Tokyo", country_code="JP")
    with pytest.raises(ValidationError, match="requested country"):
        PendingDestinationSelection(country=country(), options=(option,))


def test_pending_selection_rejects_more_than_three_options() -> None:
    with pytest.raises(ValidationError):
        PendingDestinationSelection(
            country=country(),
            options=tuple(
                CitySuggestion(
                    destination_id=uuid4(), name=f"City {index}", country_code="GB"
                )
                for index in range(4)
            ),
        )


def test_pending_selection_json_preserves_option_order_and_confirmation() -> None:
    options = tuple(
        CitySuggestion(destination_id=uuid4(), name=name, country_code="GB")
        for name in ("Second City", "First City")
    )
    pending = PendingDestinationSelection(
        country=country(confirmation=True), options=options
    )
    restored = PendingDestinationSelection.model_validate_json(
        pending.model_dump_json()
    )
    assert restored == pending
    assert restored.options == options
    assert restored.country.requires_confirmation is True


def test_pending_selection_is_immutable() -> None:
    pending = PendingDestinationSelection(country=country())
    with pytest.raises(ValidationError, match="frozen"):
        pending.country = country("JP")


def test_old_planning_state_defaults_to_no_pending_destination() -> None:
    payload = PlanningState().model_dump(mode="json")
    payload.pop("pending_destination_selection")

    restored = PlanningState.model_validate(payload)

    assert restored.pending_destination_selection is None


def test_planning_state_persists_pending_destination_options() -> None:
    options = tuple(
        CitySuggestion(
            destination_id=uuid4(),
            name=name,
            country_code="GB",
        )
        for name in ("First City", "Second City")
    )
    pending = PendingDestinationSelection(
        country=country(confirmation=True),
        options=options,
    )
    state = PlanningState(pending_destination_selection=pending)

    restored = PlanningState.model_validate_json(state.model_dump_json())

    assert restored.pending_destination_selection == pending
    assert restored.pending_destination_selection.options == options
