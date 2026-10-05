"""Deterministic completeness rules for conversational trip planning."""

from datetime import date
from enum import StrEnum

from app.domain.preferences import BudgetTier
from app.domain.trip_requirements import (
    BudgetDecision,
    TripRequirements,
    TripTransport,
)


class TripRequirementField(StrEnum):
    DESTINATION = "destination"
    START_DATE = "start_date"
    END_DATE = "end_date"
    ADULTS = "adults"
    MINOR_COUNT = "minor_count"
    MINOR_AGES = "minor_ages"
    TRANSPORT = "transport"
    ORIGIN = "origin"
    CABIN_CLASS = "cabin_class"
    INFANT_SEATING = "infant_seating"
    NEEDS_LODGING = "needs_lodging"
    ROOMS = "rooms"
    BUDGET_DECISION = "budget_decision"
    TOTAL_BUDGET = "total_budget"
    BUDGET_CURRENCY = "budget_currency"


class TripRequirementsPolicy:
    """Return missing or outdated requirements in question order."""

    @staticmethod
    def missing_fields(
        requirements: TripRequirements,
        *,
        today: date,
        budget_tier: BudgetTier | None = None,
    ) -> tuple[TripRequirementField, ...]:
        missing: list[TripRequirementField] = []

        if requirements.destination is None:
            missing.append(TripRequirementField.DESTINATION)

        if requirements.start_date is None or requirements.start_date < today:
            missing.append(TripRequirementField.START_DATE)

        end_date = requirements.resolved_end_date
        if end_date is None or end_date < today:
            missing.append(TripRequirementField.END_DATE)

        if requirements.adults is None:
            missing.append(TripRequirementField.ADULTS)

        if requirements.minor_count is None:
            missing.append(TripRequirementField.MINOR_COUNT)
        elif requirements.minor_count > 0 and requirements.minor_ages is None:
            missing.append(TripRequirementField.MINOR_AGES)

        if requirements.transport is None:
            missing.append(TripRequirementField.TRANSPORT)
        elif requirements.transport != TripTransport.OWN_ARRANGEMENTS:
            if requirements.origin is None:
                missing.append(TripRequirementField.ORIGIN)

        if requirements.transport == TripTransport.FLIGHT:
            if requirements.cabin_class is None:
                missing.append(TripRequirementField.CABIN_CLASS)

            if (
                requirements.minor_ages is not None
                and any(age < 2 for age in requirements.minor_ages)
                and requirements.infant_on_lap is None
            ):
                missing.append(TripRequirementField.INFANT_SEATING)

        if requirements.needs_lodging is None:
            missing.append(TripRequirementField.NEEDS_LODGING)
        elif requirements.needs_lodging and requirements.rooms is None:
            missing.append(TripRequirementField.ROOMS)

        if requirements.budget_decision is None:
            if budget_tier is None:
                missing.append(TripRequirementField.BUDGET_DECISION)
        elif requirements.budget_decision == BudgetDecision.SPECIFIED:
            if requirements.total_budget is None:
                missing.append(TripRequirementField.TOTAL_BUDGET)

            if requirements.budget_currency is None:
                missing.append(TripRequirementField.BUDGET_CURRENCY)

        return tuple(missing)
