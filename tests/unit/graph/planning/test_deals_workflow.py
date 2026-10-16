"""Focused tests for flexible-date deal discovery orchestration."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from app.domain.planning import PendingTravelSelection, PlanningState
from app.domain.trip_requirements import TripRequirements
from app.graph.planning.deals_workflow import (
    DealDiscoveryWorkflow,
    deal_evidence,
    match_deal_selection,
)
from app.providers.airports.schemas import AirportResolution
from app.providers.serpapi.deals_client import FlightDeal
from app.services.deal_discovery_service import (
    DealChoice,
    DealDiscoveryResult,
    DealDiscoverySnapshot,
)


def requirements(**updates: object) -> TripRequirements:
    return TripRequirements.model_validate(
        {
            "origin": "Lahore",
            "destination": "Dubai",
            "date_window_start": "2099-12-01",
            "date_window_end": "2099-12-31",
            "duration_days": 5,
            "adults": 2,
            "minor_count": 0,
            "transport": "flight",
            "cabin_class": "economy",
            "needs_lodging": False,
            "budget_decision": "undecided",
            **updates,
        }
    )


def resolved_airport(query: str, code: str) -> AirportResolution:
    return AirportResolution(status="resolved", query=query, iata_code=code)


def deal(number: int) -> DealChoice:
    return DealChoice(
        option_id=f"deal-{number}",
        deal=FlightDeal(
            origin="LHE",
            destination="DXB",
            start_date=date(2099, 12, number),
            end_date=date(2099, 12, number + 4),
            price=Decimal(200 + number),
            currency="USD",
            airline="Example Air",
            stops=1,
            flight_link="https://www.google.com/travel/flights",
        ),
    )


def available_result() -> DealDiscoveryResult:
    return DealDiscoveryResult(
        status="deals_available",
        origin=resolved_airport("Lahore", "LHE"),
        destination=resolved_airport("Dubai", "DXB"),
        options=(deal(2), deal(10)),
    )


def test_unavailable_service_returns_actionable_response() -> None:
    result = asyncio.run(
        DealDiscoveryWorkflow(deal_discovery_service=None).run(
            planning=PlanningState(requirements=requirements()),
        )
    )

    assert result == {
        "assistant_response": "Flight deal search is currently unavailable."
    }


def test_build_request_maps_children_and_infants() -> None:
    planning = PlanningState(
        requirements=requirements(
            minor_count=3,
            minor_ages=(1, 1, 6),
            infant_on_lap=(True, False),
            budget_currency="PKR",
        )
    )

    request = DealDiscoveryWorkflow._build_request(planning)

    assert request.children == 1
    assert request.infants_on_lap == 1
    assert request.infants_in_seat == 1
    assert request.currency == "PKR"


def test_run_renders_all_options_and_reuses_fresh_snapshot() -> None:
    service = AsyncMock()
    service.discover.return_value = available_result()
    workflow = DealDiscoveryWorkflow(deal_discovery_service=service)

    first = asyncio.run(
        workflow.run(
            planning=PlanningState(requirements=requirements()),
        )
    )
    second = asyncio.run(
        workflow.run(
            planning=first["planning"],
        )
    )

    assert "1. 02 Dec 2099" in first["assistant_response"]
    assert "2. 10 Dec 2099" in first["assistant_response"]
    assert "Unverified date examples" in first["assistant_response"]
    assert first["planning"].pending_travel_selection == PendingTravelSelection(
        kind="deal",
        option_ids=("deal-2", "deal-10"),
        reason="choose",
    )
    assert second["assistant_response"] == first["assistant_response"]
    service.discover.assert_awaited_once()


def test_no_deals_clears_pending_selection_and_adds_date_examples() -> None:
    service = AsyncMock()
    service.discover.return_value = DealDiscoveryResult(
        status="no_deals",
        origin=resolved_airport("Lahore", "LHE"),
        destination=resolved_airport("Dubai", "DXB"),
    )

    result = asyncio.run(
        DealDiscoveryWorkflow(deal_discovery_service=service).run(
            planning=PlanningState(requirements=requirements()),
        )
    )

    assert "No matching deals" in result["assistant_response"]
    assert "Unverified date examples" in result["assistant_response"]
    assert result["planning"].pending_travel_selection is None


def test_airport_clarification_does_not_add_unverified_dates() -> None:
    service = AsyncMock()
    service.discover.return_value = DealDiscoveryResult.model_validate(
        {
            "status": "airport_resolution_required",
            "origin": {
                "status": "resolved",
                "query": "Lahore",
                "iata_code": "LHE",
            },
            "destination": {
                "status": "selection_required",
                "query": "Dubai",
                "options": [
                    {
                        "provider_location_id": "local:DXB",
                        "iata_code": "DXB",
                        "location_type": "airport",
                        "name": "Dubai International Airport",
                        "city_name": "Dubai",
                        "country_code": "AE",
                    },
                    {
                        "provider_location_id": "local:DWC",
                        "iata_code": "DWC",
                        "location_type": "airport",
                        "name": "Al Maktoum International Airport",
                        "city_name": "Dubai",
                        "country_code": "AE",
                    },
                ],
            },
        }
    )

    result = asyncio.run(
        DealDiscoveryWorkflow(deal_discovery_service=service).run(
            planning=PlanningState(requirements=requirements()),
        )
    )

    assert "Select an airport for Dubai" in result["assistant_response"]
    assert "Unverified date examples" not in result["assistant_response"]
    assert result["clarification"] is not None
    assert len(result["clarification"].requests) == 1
    assert result["clarification"].requests[0].field == "destination_airport"


def test_match_deal_selection_accepts_number_and_exact_id() -> None:
    snapshot = DealDiscoverySnapshot(
        request_key="a" * 64,
        searched_at=datetime.now(UTC),
        result=available_result(),
    )

    assert match_deal_selection("deal 2", snapshot).option_id == "deal-10"
    assert match_deal_selection("DEAL-2", snapshot).option_id == "deal-2"
    assert match_deal_selection("deal 3", snapshot) is None


def test_deal_evidence_preserves_provider_identity_and_disclaimer() -> None:
    choice = deal(2)

    evidence = deal_evidence(choice)

    assert evidence.id == "deal-deal-2"
    assert evidence.source_id == "deal-2"
    assert tuple(map(str, evidence.source_urls)) == (
        "https://www.google.com/travel/flights",
    )
    assert "final fare are not verified" in evidence.description
