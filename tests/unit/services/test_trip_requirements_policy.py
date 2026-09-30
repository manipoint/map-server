"""Partial requirements remain strict, conditional and deterministic."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.trip_requirements import TripRequirements
from app.domain.trips import TripRequest
from app.services.trip_requirements_policy import TripRequirementField as Field
from app.services.trip_requirements_policy import TripRequirementsPolicy

TODAY = date(2027, 1, 1)


def ready(**overrides):
    return TripRequirements.model_validate(
        {
            "destination": "Hunza",
            "start_date": "2027-11-07",
            "duration_days": 4,
            "adults": 2,
            "minor_count": 0,
            "transport": "own_arrangements",
            "needs_lodging": False,
            "budget_decision": "undecided",
            **overrides,
        }
    )


def missing(requirements):
    return TripRequirementsPolicy.missing_fields(requirements, today=TODAY)


def test_partial_requirements_do_not_invent_defaults():
    value = TripRequirements(destination="Hunza", duration_days=4)
    assert value.adults is None and value.minor_count is None
    assert value.budget_currency is None and value.resolved_end_date is None
    assert missing(value) == (
        Field.START_DATE,
        Field.END_DATE,
        Field.ADULTS,
        Field.MINOR_COUNT,
        Field.TRANSPORT,
        Field.NEEDS_LODGING,
        Field.BUDGET_DECISION,
    )


def test_duration_resolves_date_without_mutating_supplied_state():
    value = ready()
    assert value.end_date is None
    assert value.resolved_end_date == date(2027, 11, 10)
    assert missing(value) == ()
    restored = TripRequirements.model_validate_json(value.model_dump_json())
    assert restored.resolved_end_date == value.resolved_end_date


@pytest.mark.parametrize(
    "start,duration,end",
    [
        ("2028-02-28", 3, date(2028, 3, 1)),
        ("2027-12-31", 2, date(2028, 1, 1)),
    ],
)
def test_date_derivation_handles_calendar_boundaries(start, duration, end):
    assert ready(start_date=start, duration_days=duration).resolved_end_date == end


@pytest.mark.parametrize(
    "values",
    [
        {"minor_ages": [True]},
        {"minor_ages": ["8"]},
        {"minor_ages": [8.0]},
        {"minor_ages": [-1]},
        {"minor_ages": [18]},
        {"minor_ages": [1], "infant_on_lap": ["false"]},
        {"minor_ages": [1], "infant_on_lap": [0]},
        {"adults": True},
        {"minor_count": "0"},
        {"needs_lodging": "false"},
    ],
)
def test_canonical_requirements_reject_coerced_facts(values):
    with pytest.raises(ValidationError):
        TripRequirements(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"end_date": "2027-11-07"},
        {"end_date": "2027-11-12"},
        {"start_date": "9999-12-31", "duration_days": 2},
        {"duration_days": 10**30},
        {"origin": "HUNZA"},
        {"adults": 100, "minor_count": 1},
        {"minor_count": 2, "minor_ages": [8]},
        {
            "minor_count": 2,
            "minor_ages": [0, 1],
            "adults": 1,
            "infant_on_lap": [True, True],
        },
        {"infant_on_lap": [True]},
        {"minor_count": 1, "minor_ages": [1], "infant_on_lap": []},
        {"needs_lodging": True, "rooms": 3},
        {"budget_decision": "no_limit", "total_budget": 100},
    ],
)
def test_contradictions_are_rejected(values):
    with pytest.raises(ValidationError):
        ready(**values)


def test_conditional_flight_and_lodging_fields():
    value = ready(transport="flight", minor_count=1, minor_ages=[1], needs_lodging=True)
    assert missing(value) == (
        Field.ORIGIN,
        Field.CABIN_CLASS,
        Field.INFANT_SEATING,
        Field.ROOMS,
    )
    complete = TripRequirements.model_validate(
        {
            **value.model_dump(),
            "origin": "Lahore",
            "cabin_class": "economy",
            "infant_on_lap": [False],
            "rooms": 1,
        }
    )
    assert missing(complete) == ()


def test_budget_decision_and_fields_are_distinct():
    assert missing(ready(budget_decision=None)) == (Field.BUDGET_DECISION,)
    assert missing(ready(budget_decision="specified")) == (
        Field.TOTAL_BUDGET,
        Field.BUDGET_CURRENCY,
    )
    assert (
        missing(
            ready(
                budget_decision="specified", total_budget="100", budget_currency="pkr"
            )
        )
        == ()
    )


def test_past_derived_dates_require_correction():
    assert missing(ready(start_date="2026-01-01")) == (Field.START_DATE, Field.END_DATE)


def test_interests_normalization_matches_final_request():
    interests = [" Food ", "food", "Nature"]
    partial = ready(interests=interests)
    final = TripRequest(
        destination="Hunza",
        start_date="2027-11-07",
        end_date="2027-11-10",
        interests=interests,
    )
    assert partial.interests == tuple(final.interests) == ("Food", "Nature")
