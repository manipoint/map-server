"""Deterministic planning evaluations; no model or provider network calls."""

import asyncio
import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.domain.planning import PlanningState
from app.domain.trip_requirements import TripRequirements
from app.graph.planning_builder import (
    build_planning_graph,
    merge_requirements,
    validate_researched_itinerary,
)
from app.graph.planning_schemas import ResearchedItinerary
from app.services.planning_research_service import PlanningResearch, ResearchEvidence


def requirements(**changes):
    return TripRequirements.model_validate(
        {
            "destination": "Japan",
            "start_date": "2099-11-07",
            "duration_days": 5,
            "adults": 2,
            "minor_count": 0,
            "transport": "own_arrangements",
            "needs_lodging": False,
            "budget_decision": "undecided",
            **changes,
        }
    )


def itinerary(days=5):
    return {
        "summary": "A draft itinerary; suggestions need confirmation.",
        "items": [
            {
                "day_number": day,
                "item_type": "activity",
                "title": "Explore at your own pace",
            }
            for day in range(1, days + 1)
        ],
    }


def run_graph(outputs, *, planning=None, message="Plan Japan"):
    gateway = AsyncMock()
    gateway.generate.side_effect = [
        AIMessage(content=json.dumps(output)) for output in outputs
    ]
    research = AsyncMock()
    research.research.return_value = PlanningResearch(searched_at=datetime.now(UTC))
    graph = build_planning_graph(model_gateway=gateway, research_service=research)
    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [],
                "locale": "en",
                "trip_id": None,
                "trip_context": None,
                "planning": planning or PlanningState(),
                "latest_message": message,
            }
        )
    )
    return result, gateway, research


def test_roman_urdu_collection_preserves_slots_and_never_searches():
    result, gateway, research = run_graph(
        [
            {
                "intent": "plan",
                "language": "ur-Latn",
                "updates": {"destination": "Hunza", "duration_days": 4},
            }
        ],
        message="Hunza ka 4 din ka trip plan kar do",
    )
    assert result["planning"].requirements.destination == "Hunza"
    assert "Janay ki tareekh" in result["assistant_response"]
    assert result["planning"].phase == "collecting"
    research.research.assert_not_awaited()
    assert gateway.generate.await_count == 1
    following, _, _ = run_graph(
        [
            {
                "intent": "plan",
                "language": "ur-Latn",
                "updates": {"adults": 2, "minor_count": 1, "minor_ages": [7]},
            }
        ],
        planning=result["planning"],
        message="2 baray aur aik bacha 7 saal ka",
    )
    assert following["planning"].requirements.duration_days == 4
    assert following["planning"].requirements.minor_ages == (7,)
    assert following["planning"].revision == 2


def test_complete_requirements_generate_without_preexisting_trip():
    result, gateway, research = run_graph(
        [
            {"intent": "plan", "updates": requirements().model_dump(mode="json")},
            itinerary(),
        ]
    )
    assert result["trip_id"] is None
    assert len(result["generated_itinerary"].items) == 5
    assert result["planning"].phase == "generated"
    assert result["planning"].research is not None
    assert gateway.generate.await_count == 2
    research.research.assert_awaited_once()


def test_general_chat_does_not_search_or_mutate_requirements():
    state = PlanningState(requirements=requirements(), phase="generated")
    result, gateway, research = run_graph(
        [{"intent": "chat", "reply": "You are welcome!"}], planning=state
    )
    assert result["planning"] == state
    assert result["assistant_response"] == "You are welcome!"
    assert gateway.generate.await_count == 1
    research.research.assert_not_awaited()


@pytest.mark.parametrize(
    "updates",
    [
        {"user_id": "attacker"},
        {"adults": 0},
        {"minor_count": 1, "minor_ages": [True]},
        {"end_date": "2099-11-20"},
    ],
)
def test_invalid_patch_preserves_previous_state(updates):
    state = PlanningState(requirements=requirements())
    result, _, research = run_graph(
        [{"intent": "plan", "updates": updates}], planning=state
    )
    assert result["planning"] == state
    assert "conflict" in result["assistant_response"]
    research.research.assert_not_awaited()


def test_explicit_new_trip_drops_old_requirements_and_revision_context():
    state = PlanningState(requirements=requirements(), itinerary=itinerary())
    result, _, _ = run_graph(
        [{"intent": "new_trip", "updates": {"destination": "Hunza"}}], planning=state
    )
    assert result["reset_trip"]
    assert result["planning"].itinerary is None
    assert result["planning"].requirements.start_date is None


def test_revision_passes_previous_itinerary_to_synthesis():
    state = PlanningState(
        requirements=requirements(), phase="generated", itinerary=itinerary()
    )
    _, gateway, _ = run_graph(
        [{"intent": "revise", "updates": {"interests": ["food"]}}, itinerary()],
        planning=state,
    )
    synthesis_input = json.loads(
        gateway.generate.await_args_list[1].kwargs["messages"][1].content
    )
    assert synthesis_input["previous_itinerary"] == itinerary()
    assert synthesis_input["requirements"]["interests"] == ["food"]


def test_bounded_repair_does_not_repeat_research():
    result, gateway, research = run_graph(
        [
            {"intent": "plan", "updates": requirements().model_dump(mode="json")},
            itinerary(1),
            itinerary(),
        ]
    )
    assert "generated_itinerary" in result
    assert gateway.generate.await_count == 3
    research.research.assert_awaited_once()


def test_second_invalid_synthesis_terminates():
    with pytest.raises(ValueError):
        run_graph(
            [
                {"intent": "plan", "updates": requirements().model_dump(mode="json")},
                itinerary(1),
                itinerary(1),
            ]
        )


def test_trip_duration_bound_prevents_research():
    result, gateway, research = run_graph(
        [
            {
                "intent": "plan",
                "updates": requirements(duration_days=31).model_dump(mode="json"),
            }
        ]
    )
    assert "30 days" in result["assistant_response"]
    research.research.assert_not_awaited()


def test_explicit_null_is_different_from_omitted_slot():
    state = requirements(origin="Lahore")
    assert merge_requirements(state, {}).origin == "Lahore"
    assert merge_requirements(state, {"origin": None}).origin is None


def test_unknown_named_place_is_rejected_and_verified_names_are_used():
    data = itinerary(2)
    data["items"][0].update(
        item_type="place", evidence_id="place-1", title="Invented name"
    )
    proposed = ResearchedItinerary.model_validate(data)
    research = PlanningResearch(searched_at=datetime.now(UTC))
    with pytest.raises(ValueError, match="evidence"):
        validate_researched_itinerary(
            proposed, requirements=requirements(duration_days=2), research=research
        )
    research = PlanningResearch(
        searched_at=datetime.now(UTC),
        evidence=(
            ResearchEvidence(
                id="place-1",
                kind="place",
                name="Verified place",
                location="Tokyo",
                description="Verified description",
            ),
        ),
    )
    result = validate_researched_itinerary(
        proposed, requirements=requirements(duration_days=2), research=research
    )
    assert result.items[0].title == "Verified place"


@pytest.mark.parametrize(
    "bad_time", ["2099-11-08T10:00:00+00:00", "2099-11-07T08:30:00+00:00"]
)
def test_wrong_day_or_overlapping_schedule_is_rejected(bad_time):
    data = itinerary(2)
    data["items"][0].update(
        starts_at="2099-11-07T08:00:00+00:00", ends_at="2099-11-07T09:00:00+00:00"
    )
    data["items"].insert(
        1,
        {
            "day_number": 1,
            "item_type": "meal",
            "title": "Breakfast",
            "starts_at": bad_time,
            "ends_at": "2099-11-08T11:00:00+00:00",
        },
    )
    with pytest.raises(ValueError):
        validate_researched_itinerary(
            ResearchedItinerary.model_validate(data),
            requirements=requirements(duration_days=2),
            research=PlanningResearch(searched_at=datetime.now(UTC)),
        )


def test_past_dates_ask_again_without_tools():
    result, _, research = run_graph(
        [
            {
                "intent": "plan",
                "updates": requirements(start_date=date(2020, 1, 1)).model_dump(
                    mode="json"
                ),
            }
        ]
    )
    assert "start_date" in result["planning"].pending_fields
    research.research.assert_not_awaited()
