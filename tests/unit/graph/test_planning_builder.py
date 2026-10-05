"""Deterministic planning evaluations; no model or provider network calls."""

import asyncio
import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.preferences import (
    BudgetTier,
    TravelInterest,
    TravelStyle,
    TripPace,
)
from app.domain.trip_requirements import TripRequirements
from app.graph.planning_builder import (
    StructuredOutputValidationError,
    build_planning_graph,
    merge_requirements,
    validate_researched_itinerary,
)
from app.graph.planning_context import PlanningRuntimeContext
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


def run_graph(
    outputs,
    *,
    planning=None,
    message="Plan Japan",
    user_message_id=None,
    persist_requirements=None,
    research_service=None,
    planning_preferences=None,
):
    # Fixture payloads explicitly identify the fields intended to change.
    outputs = [
        {**output, "changed_fields": list(output.get("updates", {}))}
        if "intent" in output and "changed_fields" not in output
        else output
        for output in outputs
    ]
    message_id = user_message_id or uuid4()
    gateway = AsyncMock()
    gateway.generate.side_effect = [
        AIMessage(content=json.dumps(output)) for output in outputs
    ]
    research = research_service or AsyncMock()
    research.cache_key = Mock(return_value="test-research-key")
    research.research.return_value = PlanningResearch(searched_at=datetime.now(UTC))

    async def persist(state, _reset_trip):
        return PlanningState.model_validate(
            {
                **state.model_dump(),
                "requirements_message_id": message_id,
            }
        )

    persistence = persist_requirements or AsyncMock(side_effect=persist)
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
                "user_message_id": message_id,
                "requirements_changed": False,
                "reset_trip": False,
                "planning_preferences": (planning_preferences or PlanningPreferences()),
            },
            context=PlanningRuntimeContext(
                persist_requirements=persistence,
            ),
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


def test_requirement_extraction_repairs_invalid_structured_output_once():
    result, gateway, research = run_graph(
        [
            {"intent": "invalid-intent"},
            {
                "intent": "plan",
                "language": "en",
                "updates": {"destination": "Japan", "duration_days": 5},
            },
        ],
        message="Plan a five-day trip to Japan",
    )

    assert gateway.generate.await_count == 2
    assert (
        "previous JSON did not match the schema"
        in gateway.generate.await_args_list[1].kwargs["messages"][0].content
    )
    assert gateway.generate.await_args_list[0].kwargs["schema"] is not None
    assert (
        "invalid-intent"
        not in gateway.generate.await_args_list[1].kwargs["messages"][0].content
    )
    assert result["planning"].requirements.destination == "Japan"
    research.research.assert_not_awaited()


def test_second_invalid_requirement_extraction_fails_safely(caplog):
    with pytest.raises(StructuredOutputValidationError):
        run_graph(
            [
                {"intent": "first-secret-invalid-intent", "updates": {}},
                {"intent": "second-secret-invalid-intent", "updates": {}},
            ]
        )
    warning = next(
        record
        for record in caplog.records
        if record.message == "Requirement extraction returned invalid structured output"
    )
    assert warning.validation_error_fields == ("intent",)
    assert warning.validation_error_types == ("literal_error",)
    assert "secret-invalid-intent" not in warning.getMessage()


def test_oversized_extraction_reply_fails_after_bounded_repair():
    with pytest.raises(StructuredOutputValidationError):
        run_graph(
            [
                {"intent": "chat", "updates": {}, "reply": "x" * 301},
                {"intent": "chat", "updates": {}, "reply": "x" * 301},
            ]
        )


def test_adults_only_party_sets_zero_minors_without_reasking_adult_count():
    """A complete adults-only party does not require a minor-count prompt."""

    result, _, research = run_graph(
        [
            {
                "intent": "plan",
                "updates": {
                    "destination": "Turkey",
                    "start_date": "2099-11-16",
                    "end_date": "2099-11-19",
                    "duration_days": 4,
                    "origin": "Lahore",
                    "adults": 3,
                    "minor_count": 0,
                },
            }
        ],
        message=(
            "Plan a 4-day trip to Turkey from Lahore, 16 November to "
            "19 November. We are 3 adults."
        ),
    )

    requirements = result["planning"].requirements
    assert requirements.adults == 3
    assert requirements.minor_count == 0
    assert "How many adults" not in result["assistant_response"]
    assert "under 18" not in result["assistant_response"]
    research.research.assert_not_awaited()


def test_adult_count_correction_preserves_existing_child_details():
    """Changing adults alone must not turn an existing family into adults only."""

    state = PlanningState(
        requirements=TripRequirements(
            destination="Turkey",
            start_date=date(2099, 11, 16),
            end_date=date(2099, 11, 19),
            adults=2,
            minor_count=1,
            minor_ages=(7,),
        )
    )
    result, _, research = run_graph(
        [{"intent": "plan", "updates": {"adults": 3}}],
        planning=state,
        message="Actually, there will be 3 adults.",
    )

    requirements = result["planning"].requirements
    assert requirements.adults == 3
    assert requirements.minor_count == 1
    assert requirements.minor_ages == (7,)
    research.research.assert_not_awaited()


def test_complete_adults_only_correction_clears_old_child_details():
    state = PlanningState(
        requirements=TripRequirements(
            destination="Turkey",
            adults=2,
            minor_count=1,
            minor_ages=(1,),
            infant_on_lap=(True,),
        )
    )
    result, _, research = run_graph(
        [{"intent": "plan", "updates": {"adults": 4, "minor_count": 0}}],
        planning=state,
        message="Actually only four adults are travelling.",
    )
    party = result["planning"].requirements
    assert party.adults == 4
    assert party.minor_count == 0
    assert party.minor_ages is None
    assert party.infant_on_lap is None
    research.research.assert_not_awaited()


def test_destination_change_clears_inferred_flight_and_cabin():
    state = PlanningState(
        requirements=requirements(
            destination="Turkey",
            origin="Lahore",
            transport="flight",
            cabin_class="economy",
        ),
        transport_inferred=True,
    )
    result, _, research = run_graph(
        [{"intent": "plan", "updates": {"destination": "Hunza"}}],
        planning=state,
    )
    updated = result["planning"]
    assert updated.requirements.transport is None
    assert updated.requirements.cabin_class is None
    assert updated.transport_inferred is False
    assert "How would you like to travel" in result["assistant_response"]
    assert "road or rail" not in result["assistant_response"]
    research.research.assert_not_awaited()


def test_destination_change_preserves_explicit_flight_choice():
    state = PlanningState(
        requirements=requirements(
            destination="Turkey",
            origin="Lahore",
            transport="flight",
            cabin_class="economy",
            needs_lodging=None,
        )
    )
    result, _, _ = run_graph(
        [{"intent": "plan", "updates": {"destination": "Japan"}}],
        planning=state,
    )
    assert result["planning"].requirements.transport == "flight"
    assert result["planning"].requirements.cabin_class == "economy"


def test_newly_inferred_flight_is_marked_for_route_revision():
    result, _, _ = run_graph(
        [
            {
                "intent": "plan",
                "transport_inferred": True,
                "updates": {"destination": "Turkey", "transport": "flight"},
            }
        ]
    )
    assert result["planning"].transport_inferred is True
    restored = PlanningState.model_validate_json(result["planning"].model_dump_json())
    assert restored.transport_inferred is True


def test_explicit_transport_correction_replaces_inferred_flight():
    state = PlanningState(
        requirements=requirements(
            transport="flight", cabin_class="economy", needs_lodging=None
        ),
        transport_inferred=True,
    )
    result, _, research = run_graph(
        [{"intent": "plan", "updates": {"transport": "own_arrangements"}}],
        planning=state,
    )
    assert result["planning"].requirements.transport == "own_arrangements"
    assert result["planning"].transport_inferred is False
    research.research.assert_not_awaited()


def test_total_party_size_without_age_split_remains_unset():
    """A total party count is insufficient to infer adults or minors."""

    result, _, research = run_graph(
        [
            {
                "intent": "plan",
                "updates": {
                    "destination": "Turkey",
                    "start_date": "2099-11-16",
                    "end_date": "2099-11-19",
                    "duration_days": 4,
                    "origin": "Lahore",
                },
            }
        ],
        message="Plan a 4-day trip to Turkey from Lahore. We are 4 people.",
    )

    requirements = result["planning"].requirements
    assert requirements.adults is None
    assert requirements.minor_count is None
    assert "How many adults" in result["assistant_response"]
    assert "under 18" in result["assistant_response"]
    research.research.assert_not_awaited()


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
    persist = AsyncMock()
    result, gateway, research = run_graph(
        [{"intent": "chat", "reply": "You are welcome!", "updates": {}}],
        planning=state,
        persist_requirements=persist,
    )
    assert result["planning"] == state
    assert result["assistant_response"] == "You are welcome!"
    assert gateway.generate.await_count == 1
    research.research.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.parametrize(
    "updates",
    [
        {"user_id": "attacker"},
        {"adults": 0},
        {"minor_count": 1, "minor_ages": [True]},
    ],
)
def test_invalid_patch_preserves_previous_state(updates):
    state = PlanningState(requirements=requirements())
    persist = AsyncMock()
    research = AsyncMock()
    with pytest.raises(StructuredOutputValidationError):
        run_graph(
            [{"intent": "plan", "updates": updates}] * 2,
            planning=state,
            persist_requirements=persist,
            research_service=research,
        )
    assert state.requirements == requirements()
    research.research.assert_not_awaited()
    persist.assert_not_awaited()


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
            research=PlanningResearch(searched_at=datetime.now(UTC), time_zone="UTC"),
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


@pytest.mark.parametrize("complete", [False, True])
def test_same_message_replay_skips_extraction_and_preserves_revision(complete):
    message_id = uuid4()
    persist = AsyncMock()
    state = PlanningState(
        requirements=requirements()
        if complete
        else TripRequirements(destination="Japan"),
        phase="ready" if complete else "collecting",
        revision=3,
        requirements_message_id=message_id,
    )
    result, gateway, research = run_graph(
        [itinerary()] if complete else [],
        planning=state,
        user_message_id=message_id,
        persist_requirements=persist,
    )
    assert gateway.generate.await_count == (1 if complete else 0)
    assert research.research.await_count == (1 if complete else 0)
    assert result["planning"].revision == 3
    assert result["planning"].requirements_message_id == message_id
    persist.assert_not_awaited()


def test_new_message_still_extracts_requirements():
    state = PlanningState(requirements_message_id=uuid4(), revision=3)
    result, gateway, _ = run_graph(
        [{"intent": "plan", "updates": {"destination": "Japan"}}],
        planning=state,
        user_message_id=uuid4(),
    )
    assert gateway.generate.await_count == 1
    assert result["planning"].revision == 4


def test_profile_interests_seed_new_trip_and_preferences_reach_extraction():
    preferences = PlanningPreferences(
        travel_styles=(TravelStyle.NATURE,),
        interests=(TravelInterest.HIKING, TravelInterest.WILDLIFE),
        budget_tier=BudgetTier.BUDGET,
        trip_pace=TripPace.RELAXED,
        home_city="Lahore",
        home_country_code="PK",
    )

    result, gateway, _ = run_graph(
        [{"intent": "plan", "updates": {"destination": "Hunza"}}],
        planning_preferences=preferences,
    )

    extraction_data = json.loads(
        gateway.generate.await_args.kwargs["messages"][1].content
    )
    assert result["planning"].requirements.interests == (
        TravelInterest.HIKING.value,
        TravelInterest.WILDLIFE.value,
    )
    assert extraction_data["profile_preferences"] == preferences.model_dump(mode="json")
    prompt = gateway.generate.await_args.kwargs["messages"][0].content
    assert "home_country_code" in prompt
    assert "transport='own_arrangements'" in prompt
    assert "needs_lodging=false" in prompt
    assert "minor_count=0" in prompt


def test_trip_specific_interests_override_profile_defaults():
    preferences = PlanningPreferences(interests=(TravelInterest.HIKING,))

    result, _, _ = run_graph(
        [
            {
                "intent": "plan",
                "updates": {
                    "destination": "Hunza",
                    "interests": ["local food"],
                },
            }
        ],
        planning_preferences=preferences,
    )

    assert result["planning"].requirements.interests == ("local food",)


def test_profile_preferences_are_included_in_itinerary_synthesis_input():
    preferences = PlanningPreferences(
        travel_styles=(TravelStyle.CULTURE,),
        interests=(TravelInterest.HISTORY,),
        budget_tier=BudgetTier.MID_RANGE,
        trip_pace=TripPace.BALANCED,
        home_city="Lahore",
        home_country_code="PK",
    )

    _, gateway, _ = run_graph(
        [
            {"intent": "plan", "updates": requirements().model_dump(mode="json")},
            itinerary(),
        ],
        planning_preferences=preferences,
    )

    synthesis_data = json.loads(
        gateway.generate.await_args_list[1].kwargs["messages"][1].content
    )
    assert synthesis_data["profile_preferences"] == preferences.model_dump(
        mode="json", exclude={"home_city", "home_country_code"}
    )


def test_valid_requirements_are_persisted_before_research():
    events = []
    message_id = uuid4()

    async def persist(state, reset_trip):
        events.append("persist")
        assert reset_trip is False
        return PlanningState.model_validate(
            {
                **state.model_dump(),
                "requirements_message_id": message_id,
            }
        )

    research = AsyncMock()

    async def perform_research(_requirements):
        events.append("research")
        return PlanningResearch(searched_at=datetime.now(UTC))

    research.research.side_effect = perform_research
    result, _, _ = run_graph(
        [
            {"intent": "plan", "updates": requirements().model_dump(mode="json")},
            itinerary(),
        ],
        user_message_id=message_id,
        persist_requirements=persist,
        research_service=research,
    )

    assert result["planning"].phase == "generated"
    assert result["planning"].requirements_message_id == message_id
    assert events == ["persist", "research"]


def test_persistence_failure_prevents_research_and_synthesis():
    persistence_error = RuntimeError("synthetic persistence failure")
    persist = AsyncMock(side_effect=persistence_error)
    research = AsyncMock()

    with pytest.raises(RuntimeError, match="synthetic persistence failure"):
        run_graph(
            [
                {
                    "intent": "plan",
                    "updates": requirements().model_dump(mode="json"),
                }
            ],
            persist_requirements=persist,
            research_service=research,
        )

    persist.assert_awaited_once()
    research.research.assert_not_awaited()


def test_new_trip_persistence_receives_reset_trip():
    state = PlanningState(requirements=requirements(), phase="generated")
    persist = AsyncMock(side_effect=lambda current, _reset: current)

    run_graph(
        [{"intent": "new_trip", "updates": {"destination": "Hunza"}}],
        planning=state,
        persist_requirements=persist,
    )

    assert persist.await_args.args[1] is True
