"""Focused tests for itinerary synthesis orchestration."""

import asyncio
import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.trip_requirements import TripRequirements
from app.graph.model_response import ModelResponseRefusedError
from app.graph.planning.synthesis_workflow import TripSynthesisWorkflow
from app.services.planning_research_service import PlanningResearch, ResearchEvidence


def requirements() -> TripRequirements:
    return TripRequirements(
        destination="Tokyo",
        start_date=date(2099, 11, 7),
        duration_days=2,
        adults=1,
        minor_count=0,
        transport="own_arrangements",
        needs_lodging=False,
        budget_decision="undecided",
    )


def itinerary(*, days: int = 2) -> dict[str, object]:
    return {
        "summary": "Model-authored draft",
        "items": [
            {
                "day_number": day_number,
                "item_type": "activity",
                "title": "Untrusted title",
                "generic_activity": "free_time",
            }
            for day_number in range(1, days + 1)
        ],
    }


def gateway_with(*responses: dict[str, object]) -> AsyncMock:
    gateway = AsyncMock()
    gateway.generate.side_effect = [
        AIMessage(content=json.dumps(response)) for response in responses
    ]
    return gateway


def test_run_builds_input_and_returns_generated_planning_state() -> None:
    gateway = gateway_with(itinerary())
    workflow = TripSynthesisWorkflow(model_gateway=gateway)
    planning = PlanningState(requirements=requirements())
    research = PlanningResearch(
        searched_at=datetime.now(UTC),
        warnings=("Weather provider unavailable.",),
    )

    result = asyncio.run(
        workflow.run(
            planning=planning,
            research=research,
            latest_message="Plan my trip",
            preferences=PlanningPreferences(),
        )
    )

    request = json.loads(gateway.generate.await_args.kwargs["messages"][1].content)
    assert request["request"] == "Plan my trip"
    assert request["requirements"]["destination"] == "Tokyo"
    assert request["max_schedule_items"] == 200
    assert request["required_selected_evidence_ids"] == []
    assert result["planning"].phase == "generated"
    assert result["planning"].itinerary is not None
    assert "Weather provider unavailable." in result["assistant_response"]
    assert "timezone could not be verified" in result["assistant_response"]


def test_invalid_first_itinerary_gets_one_repair_attempt() -> None:
    gateway = gateway_with(itinerary(days=1), itinerary())
    workflow = TripSynthesisWorkflow(model_gateway=gateway)

    result = asyncio.run(
        workflow.run(
            planning=PlanningState(requirements=requirements()),
            research=PlanningResearch(searched_at=datetime.now(UTC)),
            latest_message="Plan my trip",
            preferences=PlanningPreferences(),
        )
    )

    assert result["planning"].phase == "generated"
    assert gateway.generate.await_count == 2
    repaired_request = json.loads(
        gateway.generate.await_args_list[1].kwargs["messages"][1].content
    )
    assert "repair" in repaired_request
    assert "validation_failure" in repaired_request


def test_second_invalid_itinerary_is_propagated() -> None:
    gateway = gateway_with(itinerary(days=1), itinerary(days=1))
    workflow = TripSynthesisWorkflow(model_gateway=gateway)

    with pytest.raises(ValueError, match="cover every requested day"):
        asyncio.run(
            workflow.run(
                planning=PlanningState(requirements=requirements()),
                research=PlanningResearch(searched_at=datetime.now(UTC)),
                latest_message="Plan my trip",
                preferences=PlanningPreferences(),
            )
        )

    assert gateway.generate.await_count == 2


def test_model_refusal_is_not_repaired() -> None:
    gateway = AsyncMock()
    gateway.generate.side_effect = ModelResponseRefusedError("refused")
    workflow = TripSynthesisWorkflow(model_gateway=gateway)

    with pytest.raises(ModelResponseRefusedError):
        asyncio.run(
            workflow.run(
                planning=PlanningState(requirements=requirements()),
                research=PlanningResearch(searched_at=datetime.now(UTC)),
                latest_message="Plan my trip",
                preferences=PlanningPreferences(),
            )
        )

    gateway.generate.assert_awaited_once()


def test_required_evidence_ids_include_selected_flight_legs_and_hotel() -> None:
    planning = PlanningState(
        requirements=requirements(),
        selected_flight_id="offer-1",
        selected_hotel_id="hotel-1",
    )
    research = PlanningResearch(
        searched_at=datetime.now(UTC),
        evidence=(
            ResearchEvidence(
                id="flight-outbound",
                kind="flight",
                name="Outbound",
                location="Tokyo",
                description="Outbound flight",
                source_id="offer-1",
            ),
            ResearchEvidence(
                id="flight-return",
                kind="flight",
                name="Return",
                location="Home",
                description="Return flight",
                source_id="offer-1",
            ),
            ResearchEvidence(
                id="hotel-1",
                kind="hotel",
                name="Hotel",
                location="Tokyo",
                description="Selected hotel",
            ),
            ResearchEvidence(
                id="hotel-2",
                kind="hotel",
                name="Other hotel",
                location="Tokyo",
                description="Unselected hotel",
            ),
        ),
    )

    selected = TripSynthesisWorkflow._required_evidence_ids(
        planning=planning,
        research=research,
    )

    assert selected == {"flight-outbound", "flight-return", "hotel-1"}
