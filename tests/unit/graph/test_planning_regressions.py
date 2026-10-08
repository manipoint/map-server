"""Safety and correction cases for the production planning path."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.trip_requirements import TripRequirements
from app.graph.google_schema import google_generation_schema
from app.graph.planning_builder import (
    build_planning_graph,
    merge_requirements,
    validate_researched_itinerary,
)
from app.graph.planning_context import PlanningRuntimeContext
from app.graph.planning_schemas import RequirementExtraction, ResearchedItinerary
from app.graph.subgraphs.model_gateway import (
    FallbackModelGateway,
    ModelGatewayError,
    ModelProvider,
    model_call_budget,
)
from app.services.planning_research_service import PlanningResearch, ResearchEvidence
from app.services.standalone_search_service import SearchReply


def requirements(**updates):
    return TripRequirements.model_validate(
        dict(
            destination="Tokyo",
            start_date="2099-11-07",
            duration_days=2,
            adults=2,
            minor_count=0,
            transport="own_arrangements",
            needs_lodging=False,
            budget_decision="undecided",
            **updates,
        )
    )


def proposal(**item_updates):
    return ResearchedItinerary.model_validate(
        {
            "summary": "Booking confirmed and within budget!",
            "items": [
                {
                    "day_number": 1,
                    "item_type": "activity",
                    "title": "Invented Hotel reservation confirmed",
                    **item_updates,
                },
                {"day_number": 2, "item_type": "activity", "title": "Explore"},
            ],
        }
    )


def test_google_patch_nulls_do_not_clear_unselected_fields():
    schema = google_generation_schema(RequirementExtraction)
    assert '"oneOf"' not in json.dumps(schema)
    patch = schema["$defs"]["TripRequirementsPatch"]
    assert set(patch["properties"]) == set(TripRequirements.model_fields)
    assert not patch.get("required")
    values = {key: None for key in patch["properties"]}
    values["destination"] = "Osaka"
    extracted = RequirementExtraction(
        intent="revise", updates=values, changed_fields=["destination"]
    )
    result = merge_requirements(requirements(), extracted.selected_updates())
    assert result.destination == "Osaka"
    assert result.adults == 2 and result.duration_days == 2
    assert result.needs_lodging is False


@pytest.mark.parametrize("changed", [["origin", "origin"], ["owner_id"], ["adults"]])
def test_change_mask_rejects_duplicate_unknown_and_absent_values(changed):
    with pytest.raises(ValidationError):
        RequirementExtraction(intent="plan", updates={}, changed_fields=changed)


def test_date_budget_and_party_corrections_clear_dependent_values():
    current = requirements()
    current = merge_requirements(current, {"end_date": "2099-11-10"})
    assert (
        current.duration_days is None
        and current.resolved_end_date.isoformat() == "2099-11-10"
    )
    current = merge_requirements(current, {"duration_days": 5})
    assert (
        current.end_date is None
        and current.resolved_end_date.isoformat() == "2099-11-11"
    )
    current = merge_requirements(
        current,
        {"budget_decision": "specified", "total_budget": 500, "budget_currency": "USD"},
    )
    current = merge_requirements(current, {"budget_decision": "no_limit"})
    assert current.total_budget is None and current.budget_currency is None
    current = merge_requirements(current, {"minor_count": 1, "minor_ages": [4]})
    current = merge_requirements(current, {"minor_count": 2})
    assert current.minor_ages is None


def test_explicit_conflicting_dates_are_not_silently_discarded():
    with pytest.raises(ValidationError):
        merge_requirements(
            requirements(),
            {"start_date": "2099-11-07", "end_date": "2099-11-11", "duration_days": 2},
        )


def test_generic_items_and_summary_cannot_smuggle_booking_claims():
    result = validate_researched_itinerary(
        proposal(),
        requirements=requirements(),
        research=PlanningResearch(searched_at=datetime.now(UTC)),
    )
    assert "confirmed" not in result.model_dump_json()
    assert "Invented Hotel" not in result.model_dump_json()
    assert result.items[0].title == "Free time"


def test_model_cannot_supply_image_urls_or_timezone_identifiers():
    with pytest.raises(ValidationError):
        proposal(
            image={"url": "https://untrusted.example/fake.jpg", "alt_text": "Fake"}
        )
    with pytest.raises(ValidationError):
        proposal(start_time_zone="Asia/Tokyo")
    schema = google_generation_schema(ResearchedItinerary)
    assert '"format": "uri"' not in json.dumps(schema)


def test_unknown_destination_timezone_omits_unverified_schedule():
    result = validate_researched_itinerary(
        proposal(
            starts_at="2099-11-07T08:00:00+07:00", ends_at="2099-11-07T09:00:00+07:00"
        ),
        requirements=requirements(),
        research=PlanningResearch(searched_at=datetime.now(UTC)),
    )
    assert result.items[0].starts_at is None


def test_verified_timezone_rejects_invented_offset():
    with pytest.raises(ValueError, match="offset"):
        validate_researched_itinerary(
            proposal(
                starts_at="2099-11-07T08:00:00+07:00",
                ends_at="2099-11-07T09:00:00+07:00",
            ),
            requirements=requirements(),
            research=PlanningResearch(
                searched_at=datetime.now(UTC), time_zone="Asia/Tokyo"
            ),
        )


def test_overnight_flight_times_come_from_evidence():
    source = ResearchEvidence(
        id="flight-1",
        kind="flight",
        name="LHR to HND",
        location="HND",
        description="Unbooked quote",
        starts_at="2099-11-07T23:00:00Z",
        ends_at="2099-11-08T14:00:00+09:00",
        start_time_zone="Europe/London",
        end_time_zone="Asia/Tokyo",
    )
    result = validate_researched_itinerary(
        proposal(
            item_type="flight",
            evidence_id="flight-1",
            starts_at="2099-11-07T08:00:00Z",
            ends_at="2099-11-07T09:00:00Z",
        ),
        requirements=requirements(),
        research=PlanningResearch(searched_at=datetime.now(UTC), evidence=(source,)),
    )
    assert result.items[0].starts_at == source.starts_at
    assert result.items[0].ends_at == source.ends_at
    assert result.items[0].end_time_zone == "Asia/Tokyo"


def run_graph(outputs, state, research, standalone=None, preferences=None):
    gateway = AsyncMock()
    gateway.generate.side_effect = [
        AIMessage(content=json.dumps(output)) for output in outputs
    ]
    graph = build_planning_graph(
        model_gateway=gateway, research_service=research, standalone_service=standalone
    )
    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [],
                "planning": state,
                "latest_message": "Change my plan",
                "user_message_id": uuid4(),
                "planning_preferences": preferences or PlanningPreferences(),
            },
            context=PlanningRuntimeContext(
                persist_requirements=AsyncMock(side_effect=lambda value, reset: value)
            ),
        )
    )
    return result, gateway


def test_standalone_search_does_not_require_or_mutate_a_trip():
    service = AsyncMock()
    service.search.return_value = SearchReply(content="Verified current weather")
    state = PlanningState(requirements=TripRequirements(destination="Hunza"))
    research = AsyncMock()
    result, gateway = run_graph(
        [
            {
                "intent": "search",
                "updates": {},
                "changed_fields": [],
                "search": {"kind": "weather", "arguments": {"city": "Lahore"}},
            }
        ],
        state,
        research,
        service,
    )
    assert result["assistant_response"] == "Verified current weather"
    assert result["planning"].requirements == state.requirements
    service.search.assert_awaited_once()
    research.research.assert_not_awaited()
    gateway.generate.assert_awaited_once()


@pytest.mark.parametrize("expired", [False, True])
def test_minor_revision_reuses_only_fresh_research(expired):
    cached = PlanningResearch(
        searched_at=datetime.now(UTC) - timedelta(minutes=6 if expired else 1)
    )
    state = PlanningState(
        requirements=requirements(),
        research=cached.model_dump(mode="json"),
        research_key="same",
        interests_overridden=True,
    )
    research = AsyncMock()
    research.cache_key = Mock(return_value="same")
    research.research.return_value = PlanningResearch(searched_at=datetime.now(UTC))
    result, _ = run_graph(
        [
            {
                "intent": "revise",
                "updates": {
                    "trip_pace": "relaxed",
                    "constraints": ["step-free access", "vegetarian"],
                },
                "changed_fields": ["trip_pace", "constraints"],
            },
            proposal().model_dump(mode="json"),
        ],
        state,
        research,
        preferences=PlanningPreferences(interests=("history",)),
    )
    assert research.research.await_count == int(expired)
    assert result["planning"].requirements.interests == ()
    assert result["planning"].requirements.constraints == (
        "step-free access",
        "vegetarian",
    )


def test_research_guidance_stops_before_synthesis():
    research = AsyncMock()
    research.cache_key = Mock(return_value="key")
    research.research.return_value = PlanningResearch(
        searched_at=datetime.now(UTC),
        guidance=(
            SearchReply(content="Which city in which country?", input_required=True),
        ),
    )
    result, gateway = run_graph(
        [{"intent": "plan", "updates": {}, "changed_fields": []}],
        PlanningState(requirements=requirements()),
        research,
    )
    assert result["assistant_response"] == "Which city in which country?"
    assert "generated_itinerary" not in result
    gateway.generate.assert_awaited_once()


def test_model_budget_and_input_limit_prevent_extra_provider_calls():
    async def scenario():
        client = AsyncMock()
        client.ainvoke.return_value = AIMessage(content="Hello")
        gateway = FallbackModelGateway(
            [ModelProvider("test", client)], max_input_chars=10
        )
        with pytest.raises(ModelGatewayError, match="input"):
            await gateway.generate(messages=[HumanMessage(content="x" * 11)])
        client.ainvoke.assert_not_awaited()
        with model_call_budget(1):
            await gateway.generate(messages=[HumanMessage(content="Hi")])
            with pytest.raises(ModelGatewayError, match="budget"):
                await gateway.generate(messages=[HumanMessage(content="Hi")])
        client.ainvoke.assert_awaited_once()

    asyncio.run(scenario())
