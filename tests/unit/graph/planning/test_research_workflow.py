"""Research cache, selection state, and provider dispatch regressions."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, create_autospec

import pytest

from app.domain.clarifications import AirportInputRequest, TravelClarification
from app.domain.planning import PendingTravelSelection, PlanningState
from app.domain.trip_requirements import TripRequirements
from app.graph.planning.deals_workflow import deal_evidence
from app.graph.planning.research_workflow import (
    PlanningResearchWorkflow,
    ResearchWorkflowResult,
)
from app.providers.serpapi.deals_client import FlightDeal
from app.services.deal_discovery_service import DealChoice
from app.services.planning_research_service import (
    PlanningResearch,
    PlanningResearchService,
    ResearchEvidence,
)
from app.services.standalone_search_service import SearchReply

NOW = datetime(2099, 10, 1, tzinfo=UTC)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr("app.graph.planning.research_workflow.utc_now", lambda: NOW)
    mocked = create_autospec(PlanningResearchService, instance=True)
    mocked.cache_key.return_value = "current-key"
    mocked.research.return_value = PlanningResearch(searched_at=NOW)
    return mocked


def planning(**updates: object) -> PlanningState:
    return PlanningState.model_validate(
        {
            "requirements": {
                "destination": "Tokyo",
                "origin": "London",
                "start_date": "2099-11-07",
                "duration_days": 5,
                "adults": 2,
                "minor_count": 0,
                "transport": "own_arrangements",
                "needs_lodging": False,
                "budget_decision": "undecided",
            },
            **updates,
        }
    )


def with_requirements(**updates: object) -> PlanningState:
    current = planning()
    return current.model_copy(
        update={
            "requirements": TripRequirements.model_validate(
                {**current.requirements.model_dump(), **updates}
            )
        }
    )


def evidence(kind: str, item_id: str, **updates: object) -> ResearchEvidence:
    return ResearchEvidence.model_validate(
        {
            "id": item_id,
            "kind": kind,
            "name": item_id,
            "location": "Tokyo",
            "description": f"Verified {item_id}",
            **updates,
        }
    )


def run(service: Mock, state: PlanningState) -> ResearchWorkflowResult:
    return asyncio.run(
        PlanningResearchWorkflow(research_service=service).run(planning=state)
    )


def test_fresh_cache_is_returned_without_provider_call(service: Mock) -> None:
    cached = PlanningResearch(
        searched_at=NOW - timedelta(minutes=4),
        evidence=(evidence("place", "place-1"),),
    )
    state = planning(
        research=cached.model_dump(mode="json"), research_key="current-key"
    )

    result = run(service, state)

    service.research.assert_not_awaited()
    assert result["route"] == "synthesize"
    assert result["research"] == cached
    assert result["planning"].research == cached.model_dump(mode="json")


@pytest.mark.parametrize(
    ("searched_at", "cache_key", "expires_at"),
    [
        (NOW - timedelta(minutes=5), "current-key", None),
        (NOW + timedelta(seconds=1), "current-key", None),
        (NOW, "old-key", None),
        (NOW, "current-key", NOW),
        (NOW, "current-key", NOW - timedelta(seconds=1)),
    ],
)
def test_selection_does_not_bypass_cache_freshness(
    service: Mock,
    searched_at: datetime,
    cache_key: str,
    expires_at: datetime | None,
) -> None:
    cached = PlanningResearch(
        searched_at=searched_at,
        evidence=(evidence("hotel", "hotel-1", expires_at=expires_at),),
    )
    state = planning(
        research=cached.model_dump(mode="json"),
        research_key=cache_key,
        pending_travel_selection=PendingTravelSelection(
            kind="hotel", option_ids=("hotel-1",), reason="choose"
        ),
    )

    run(service, state)

    service.research.assert_awaited_once_with(state.requirements)


@pytest.mark.parametrize("has_guidance", [False, True])
def test_warnings_or_guidance_prevent_normal_cache_reuse(
    service: Mock, has_guidance: bool
) -> None:
    cached = PlanningResearch(
        searched_at=NOW,
        warnings=() if has_guidance else ("Weather provider unavailable",),
        guidance=(SearchReply(content="Select an airport"),) if has_guidance else (),
    )
    state = planning(
        research=cached.model_dump(mode="json"), research_key="current-key"
    )

    run(service, state)

    service.research.assert_awaited_once_with(state.requirements)


@pytest.mark.parametrize(
    "selection",
    [
        {"pending_travel_selection": {"kind": "hotel", "reason": "no_results"}},
        {"selected_flight_id": "flight-1"},
        {"selected_hotel_id": "hotel-1"},
    ],
)
def test_selection_reuses_fresh_cache_despite_unrelated_warning(
    service: Mock, selection: dict[str, object]
) -> None:
    cached = PlanningResearch(
        searched_at=NOW,
        warnings=("Weather provider unavailable",),
        evidence=(evidence("place", "place-1"),),
    )
    state = planning(
        research=cached.model_dump(mode="json"),
        research_key="current-key",
        **selection,
    )

    result = run(service, state)

    service.research.assert_not_awaited()
    assert result["research"].warnings == cached.warnings


def test_guidance_stops_selection_and_joins_actual_newlines(service: Mock) -> None:
    clarification = TravelClarification(
        requests=[
            AirportInputRequest(
                field="destination_airport",
                query="Tokyo",
                status="not_found",
                question="Please clarify Tokyo.",
            )
        ]
    )
    researched = PlanningResearch(
        searched_at=NOW,
        evidence=(evidence("flight", "flight-1"), evidence("flight", "flight-2")),
        guidance=(
            SearchReply(content="First guidance"),
            SearchReply(content="Second guidance", clarification=clarification),
        ),
    )
    service.research.return_value = researched

    result = run(service, with_requirements(transport="flight", cabin_class="economy"))

    assert result["route"] == "respond"
    assert result["assistant_response"] == "First guidance\nSecond guidance"
    assert result["clarification"] == clarification
    assert "planning" not in result


@pytest.mark.parametrize("kind", ["flight", "hotel"])
def test_multiple_options_pause_for_explicit_selection(
    service: Mock, kind: str
) -> None:
    service.research.return_value = PlanningResearch(
        searched_at=NOW,
        evidence=(evidence(kind, "option-1"), evidence(kind, "option-2")),
    )
    state = with_requirements(
        transport="flight" if kind == "flight" else "own_arrangements",
        cabin_class="economy" if kind == "flight" else None,
        needs_lodging=kind == "hotel",
        rooms=1 if kind == "hotel" else None,
    )

    result = run(service, state)

    assert result["route"] == "respond"
    assert result["planning"].pending_travel_selection == PendingTravelSelection(
        kind=kind, option_ids=("option-1", "option-2"), reason="choose"
    )
    assert result["planning"].research_key == "current-key"
    assert "1. option-1" in result["assistant_response"]
    assert "2. option-2" in result["assistant_response"]


def test_auto_selected_flight_survives_hotel_prompt_and_followup(service: Mock) -> None:
    researched = PlanningResearch(
        searched_at=NOW,
        evidence=(
            evidence("flight", "outbound", source_id="offer-1"),
            evidence("flight", "outbound-return", source_id="offer-1"),
            evidence("hotel", "hotel-1"),
            evidence("hotel", "hotel-2"),
            evidence("place", "place-1"),
        ),
    )
    service.research.return_value = researched
    state = with_requirements(
        transport="flight", cabin_class="economy", needs_lodging=True, rooms=1
    )
    original = state.model_dump(mode="json")

    first = run(service, state)

    assert first["route"] == "respond"
    assert first["planning"].selected_flight_id == "offer-1"
    assert first["planning"].pending_travel_selection.kind == "hotel"
    followup = first["planning"].model_copy(update={"selected_hotel_id": "hotel-2"})
    second = run(service, followup)

    service.research.assert_awaited_once()
    assert second["route"] == "synthesize"
    assert second["planning"].selected_flight_id == "offer-1"
    assert second["planning"].selected_hotel_id == "hotel-2"
    assert second["planning"].pending_travel_selection is None
    assert [item.id for item in second["research"].evidence] == [
        "outbound",
        "outbound-return",
        "hotel-2",
        "place-1",
    ]
    assert state.model_dump(mode="json") == original


def test_single_hotel_is_auto_selected(service: Mock) -> None:
    service.research.return_value = PlanningResearch(
        searched_at=NOW, evidence=(evidence("hotel", "hotel-1"),)
    )

    result = run(service, with_requirements(needs_lodging=True, rooms=1))

    assert result["route"] == "synthesize"
    assert result["planning"].selected_hotel_id == "hotel-1"
    assert result["research"].evidence[0].id == "hotel-1"


@pytest.mark.parametrize("kind", ["flight", "hotel"])
def test_verified_empty_results_pause_but_provider_failures_allow_draft(
    service: Mock, kind: str
) -> None:
    state = with_requirements(
        transport="flight" if kind == "flight" else "own_arrangements",
        cabin_class="economy" if kind == "flight" else None,
        needs_lodging=kind == "hotel",
        rooms=1 if kind == "hotel" else None,
    )
    service.research.return_value = PlanningResearch(
        searched_at=NOW, warnings=(f"{kind}s: no verified options",)
    )

    empty = run(service, state)

    assert empty["route"] == "respond"
    assert empty["planning"].pending_travel_selection.reason == "no_results"
    assert f"No verified {kind}s" in empty["assistant_response"]
    service.research.return_value = PlanningResearch(
        searched_at=NOW, warnings=(f"{kind}s: provider unavailable",)
    )

    failed = run(service, state)

    assert failed["route"] == "synthesize"
    assert failed["research"].warnings == (f"{kind}s: provider unavailable",)


@pytest.mark.parametrize(
    ("warning", "expected"),
    [
        ("flights: nearby-date checks failed", "provider issue"),
        ("flights: one-day-shifted trip dates", "one day earlier and later"),
        (
            "flights: nearby date suggestions were already checked",
            "one day earlier and later",
        ),
    ],
)
def test_no_flight_response_explains_nearby_search(
    service: Mock, warning: str, expected: str
) -> None:
    service.research.return_value = PlanningResearch(
        searched_at=NOW, warnings=("flights: no verified options", warning)
    )

    result = run(service, with_requirements(transport="flight", cabin_class="economy"))

    assert expected in result["assistant_response"]
    assert result["planning"].nearby_flight_dates_checked is True


@pytest.mark.parametrize("selected_id", [None, "expired-offer"])
def test_nearby_dates_are_offered_after_invalidating_stale_selection(
    service: Mock, selected_id: str | None
) -> None:
    service.research.return_value = PlanningResearch(
        searched_at=NOW,
        evidence=(
            evidence(
                "flight",
                "nearby-flight",
                alternative_start_date=date(2099, 11, 8),
                alternative_end_date=date(2099, 11, 12),
            ),
        ),
    )
    state = with_requirements(transport="flight", cabin_class="economy").model_copy(
        update={"selected_flight_id": selected_id}
    )

    result = run(service, state)

    assert result["route"] == "respond"
    assert result["planning"].pending_travel_selection.kind == "flight_dates"
    assert result["planning"].pending_travel_selection.option_ids == (
        "2099-11-08|2099-11-12",
    )
    assert result["planning"].selected_flight_id is None
    assert result["planning"].nearby_flight_dates_checked is True
    assert result["planning"].requirements == state.requirements


def test_already_checked_nearby_dates_disable_repeat_provider_check(
    service: Mock,
) -> None:
    state = planning(nearby_flight_dates_checked=True)

    run(service, state)

    service.research.assert_awaited_once_with(
        state.requirements, allow_nearby_flight_dates=False
    )


@pytest.mark.parametrize("cache_includes_deal", [False, True])
def test_selected_deal_is_validated_and_required_for_cache_reuse(
    service: Mock, cache_includes_deal: bool
) -> None:
    choice = DealChoice(
        option_id="deal-1",
        deal=FlightDeal(
            origin="LHR",
            destination="HND",
            start_date=date(2099, 11, 7),
            end_date=date(2099, 11, 11),
            price=Decimal(500),
            currency="USD",
            flight_link="https://www.google.com/travel/flights",
        ),
    )
    selected = deal_evidence(choice)
    researched = PlanningResearch(searched_at=NOW, evidence=(selected,))
    cached = researched if cache_includes_deal else PlanningResearch(searched_at=NOW)
    service.research.return_value = researched
    state = with_requirements(transport="flight", cabin_class="economy").model_copy(
        update={
            "selected_deal": choice.model_dump(mode="json"),
            "selected_flight_id": "deal-1",
            "research": cached.model_dump(mode="json"),
            "research_key": "current-key",
        }
    )

    result = run(service, state)

    if cache_includes_deal:
        service.research.assert_not_awaited()
    else:
        service.research.assert_awaited_once_with(
            state.requirements, preselected_flight=selected
        )
    assert result["route"] == "synthesize"
    assert result["research"].evidence == (selected,)


@pytest.mark.parametrize("error_type", [TimeoutError, asyncio.CancelledError])
def test_provider_failure_or_cancellation_propagates(
    service: Mock, error_type: type[BaseException]
) -> None:
    service.research.side_effect = error_type()

    with pytest.raises(error_type):
        run(service, planning())

    service.research.assert_awaited_once()
