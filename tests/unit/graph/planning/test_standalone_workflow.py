"""Regression tests for standalone search state handling."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.domain.planning import PendingTravelSelection, PlanningState
from app.graph.planning.standalone_workflow import StandaloneSearchWorkflow
from app.providers.airports.schemas import AirportResolution
from app.services.deal_discovery_service import DealDiscoveryResult
from app.services.standalone_search_service import (
    FlightDealsRequest,
    SearchReply,
    StandaloneDealsInput,
)


def flight_deals_request() -> FlightDealsRequest:
    return FlightDealsRequest(
        kind="flight_deals",
        arguments=StandaloneDealsInput(
            origin="London",
            destination="Tokyo",
            window_start=date(2099, 11, 1),
            window_end=date(2099, 11, 30),
            adults=1,
        ),
    )


def planning_with_pending_deal() -> PlanningState:
    return PlanningState(
        pending_travel_selection=PendingTravelSelection(
            kind="deal", option_ids=("old-deal",), reason="choose"
        )
    )


@pytest.mark.parametrize(
    "reply",
    [
        SearchReply(
            content="Please clarify the destination airport.", input_required=True
        ),
        SearchReply(content="No verified deals were returned."),
    ],
)
def test_missing_deal_result_does_not_create_pending_selection(
    reply: SearchReply,
) -> None:
    service = AsyncMock()
    service.search.return_value = reply
    workflow = StandaloneSearchWorkflow(standalone_service=service)
    planning = planning_with_pending_deal()

    result = asyncio.run(
        workflow.run(request=flight_deals_request(), planning=planning)
    )

    service.search.assert_awaited_once()
    assert result["assistant_response"] == reply.content
    assert result["planning"].pending_travel_selection is None
    assert result["planning"].deal_discovery is None
    assert result["planning"].pending_search == (
        flight_deals_request().model_dump(mode="json") if reply.input_required else None
    )


def test_airport_resolution_result_does_not_create_empty_deal_selection() -> None:
    airport_result = DealDiscoveryResult(
        status="airport_resolution_required",
        origin=AirportResolution(status="resolved", query="London", iata_code="LHR"),
        destination=AirportResolution(status="not_found", query="Tokyo", options=()),
    )
    reply = SearchReply(
        content="Please clarify the destination airport.",
        input_required=True,
        deal_result=airport_result,
    )
    service = AsyncMock()
    service.search.return_value = reply
    workflow = StandaloneSearchWorkflow(standalone_service=service)

    result = asyncio.run(
        workflow.run(
            request=flight_deals_request(), planning=planning_with_pending_deal()
        )
    )

    assert result["assistant_response"] == reply.content
    assert result["clarification"] is None
    assert result["planning"].deal_discovery is None
    assert result["planning"].pending_travel_selection is None
    assert result["planning"].pending_search is not None
