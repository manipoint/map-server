"""Exercise deal discovery and selection across serialized conversation state."""

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.graph.planning_builder import build_planning_graph
from app.graph.planning_context import PlanningRuntimeContext
from app.providers.airports.schemas import AirportResolution
from app.providers.serpapi.deals_client import FlightDeal
from app.services.deal_discovery_service import DealChoice, DealDiscoveryResult
from app.services.planning_research_service import PlanningResearch
from app.services.standalone_search_service import SearchReply


def test_standalone_deal_selection_survives_state_round_trip_without_new_flight_search():
    deal = DealChoice(
        option_id="deal-1",
        deal=FlightDeal(
            origin="LHE",
            destination="DXB",
            start_date=date(2099, 12, 10),
            end_date=date(2099, 12, 14),
            price=Decimal("220"),
            currency="USD",
            airline="Example Air",
            flight_link="https://www.google.com/travel/flights",
        ),
    )
    deal_result = DealDiscoveryResult(
        status="deals_available",
        origin=AirportResolution(status="resolved", query="Lahore", iata_code="LHE"),
        destination=AirportResolution(
            status="resolved", query="Dubai", iata_code="DXB"
        ),
        options=(deal,),
    )
    standalone = AsyncMock()
    standalone.search.return_value = SearchReply(
        content="1. 10 Dec 2099 to 14 Dec 2099, 220 USD",
        deal_result=deal_result,
    )
    gateway = AsyncMock()
    gateway.generate.return_value = AIMessage(
        content=json.dumps(
            {
                "intent": "search",
                "updates": {},
                "changed_fields": [],
                "search": {
                    "kind": "flight_deals",
                    "arguments": {
                        "origin": "Lahore",
                        "destination": "Dubai",
                        "window_start": "2099-12-01",
                        "window_end": "2099-12-31",
                    },
                },
            }
        )
    )
    research = AsyncMock()
    research.cache_key = Mock(return_value="selected-deal-research")
    research.research.return_value = PlanningResearch(searched_at=datetime.now(UTC))
    graph = build_planning_graph(
        model_gateway=gateway,
        research_service=research,
        standalone_service=standalone,
    )

    async def persist(state: PlanningState, _reset_trip: bool) -> PlanningState:
        return PlanningState.model_validate(state.model_dump(mode="json"))

    async def invoke(message: str, planning: PlanningState) -> dict[str, object]:
        return await graph.ainvoke(
            {
                "messages": [],
                "locale": "en",
                "trip_id": None,
                "trip_context": None,
                "planning": planning,
                "latest_message": message,
                "user_message_id": uuid4(),
                "requirements_changed": False,
                "reset_trip": False,
                "planning_preferences": PlanningPreferences(),
            },
            context=PlanningRuntimeContext(persist_requirements=persist),
        )

    async def exercise() -> tuple[dict[str, object], dict[str, object]]:
        offered = await invoke(
            "Find Lahore to Dubai flight deals in December", PlanningState()
        )
        restored = PlanningState.model_validate(
            json.loads(offered["planning"].model_dump_json())
        )
        selected = await invoke("deal 1", restored)
        return offered, selected

    offered, selected = asyncio.run(exercise())

    assert offered["planning"].pending_travel_selection.option_ids == ("deal-1",)
    requirements = selected["planning"].requirements
    assert requirements.origin == "Lahore"
    assert requirements.destination == "Dubai"
    assert requirements.start_date == date(2099, 12, 10)
    assert requirements.end_date == date(2099, 12, 14)
    assert selected["planning"].selected_deal["option_id"] == "deal-1"
    standalone.search.assert_awaited_once()
    gateway.generate.assert_awaited_once()
    research.research.assert_not_awaited()
