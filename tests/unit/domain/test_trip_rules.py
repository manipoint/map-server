"""Shared rules preserve boundary-specific date and scalar contracts."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.api.schemas.trips import TripCreateRequest
from app.domain.trip_requirements import TripRequirements
from app.domain.trips import ChildAge, InfantAge, TripRequest, TripUpdate
from app.graph.schemas.trips import ActiveTripContext
from app.providers.flights.schemas import ChildAge as FlightChildAge
from app.providers.flights.schemas import FlightSearchInput
from app.providers.flights.schemas import InfantAge as FlightInfantAge
from app.services.flight_search_preparation_service import FlightSearchPreparationInput


def test_legacy_age_imports_reexport_the_same_types():
    assert ChildAge is FlightChildAge
    assert InfantAge is FlightInfantAge


@pytest.mark.parametrize(
    "model",
    [TripRequest, TripRequirements, TripCreateRequest, ActiveTripContext, TripUpdate],
)
def test_trip_date_rule_is_consistent(model):
    with pytest.raises(ValidationError, match="end_date must be after start_date"):
        model(
            destination="Hunza", start_date=date(2027, 1, 1), end_date=date(2027, 1, 1)
        )


@pytest.mark.parametrize("model", [FlightSearchInput, FlightSearchPreparationInput])
def test_same_day_flights_and_legacy_age_coercion_remain_supported(model):
    value = model(
        origin="LHE",
        destination="KHI",
        departure_date="2027-11-07",
        return_date="2027-11-07",
        children_ages=["8"],
    )
    assert value.children_ages == [8]
    assert value.return_date == value.departure_date


@pytest.mark.parametrize("model", [FlightSearchInput, FlightSearchPreparationInput])
def test_lap_infant_rule_is_shared(model):
    with pytest.raises(ValidationError, match="each lap infant"):
        model(
            origin="LHE",
            destination="KHI",
            departure_date="2027-11-07",
            adults=1,
            infants_on_lap_ages=[0, 1],
        )
