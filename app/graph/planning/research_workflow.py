"""Run bounded research and preserve deterministic travel selections."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, NotRequired, TypedDict

from app.common.time import utc_now
from app.domain.clarifications import TravelClarification
from app.domain.planning import PendingTravelSelection, PlanningState
from app.graph.planning.deals_workflow import deal_evidence
from app.graph.planning.travel_selection import (
    flight_date_options,
    flight_date_selection_text,
    has_no_verified_options,
    research_for_selection,
    selection_text,
    travel_options,
)
from app.services.deal_discovery_service import DealChoice
from app.services.planning_research_service import (
    PlanningResearch,
    PlanningResearchService,
    ResearchEvidence,
)

TravelKind = Literal["flight", "hotel"]
RESEARCH_CACHE_TTL = timedelta(minutes=5)


class ResearchWorkflowResult(TypedDict):
    research: PlanningResearch
    route: Literal["respond", "synthesize"]
    planning: NotRequired[PlanningState]
    assistant_response: NotRequired[str]
    clarification: NotRequired[TravelClarification | None]


@dataclass(frozen=True)
class TravelSelectionDecision:
    selected_id: str | None
    pending: PendingTravelSelection | None


@dataclass(frozen=True)
class SelectionResult:
    selected_id: str | None
    response: ResearchWorkflowResult | None


class PlanningResearchWorkflow:
    """Run or reuse bounded research and resolve travel selections."""

    def __init__(self, *, research_service: PlanningResearchService) -> None:
        self._service = research_service

    async def run(self, *, planning: PlanningState) -> ResearchWorkflowResult:
        selected_evidence = self._selected_deal_evidence(planning)
        cache_key = self._service.cache_key(planning.requirements)
        cached = self._cached_research(planning)
        if cached is not None and self._is_reusable(
            planning=planning,
            cached=cached,
            cache_key=cache_key,
            selected_evidence=selected_evidence,
            now=utc_now(),
        ):
            result = cached
        else:
            result = await self._run_research(
                planning=planning, selected_evidence=selected_evidence
            )

        if result.guidance:
            return self._guidance_result(result)

        flight = self._resolve_flight_selection(
            planning=planning, research=result, cache_key=cache_key
        )
        if flight.response is not None:
            return flight.response

        hotel = self._resolve_hotel_selection(
            planning=planning,
            research=result,
            cache_key=cache_key,
            selected_flight_id=flight.selected_id,
        )
        if hotel.response is not None:
            return hotel.response

        return self._synthesis_result(
            planning=planning,
            research=result,
            cache_key=cache_key,
            selected_flight_id=flight.selected_id,
            selected_hotel_id=hotel.selected_id,
        )

    @staticmethod
    def _selected_deal_evidence(planning: PlanningState) -> ResearchEvidence | None:
        if planning.selected_deal is None:
            return None
        return deal_evidence(DealChoice.model_validate(planning.selected_deal))

    @staticmethod
    def _cached_research(planning: PlanningState) -> PlanningResearch | None:
        return (
            PlanningResearch.model_validate(planning.research)
            if planning.research is not None
            else None
        )

    @staticmethod
    def _is_reusable(
        *,
        planning: PlanningState,
        cached: PlanningResearch,
        cache_key: str,
        selected_evidence: ResearchEvidence | None,
        now: datetime,
    ) -> bool:
        if (
            planning.research_key != cache_key
            or not timedelta(0) <= now - cached.searched_at < RESEARCH_CACHE_TTL
            or any(
                item.expires_at is not None and item.expires_at <= now
                for item in cached.evidence
            )
            or (
                selected_evidence is not None
                and not any(item.id == selected_evidence.id for item in cached.evidence)
            )
        ):
            return False
        has_selection = (
            planning.pending_travel_selection is not None
            or planning.selected_flight_id is not None
            or planning.selected_hotel_id is not None
        )
        return has_selection or (not cached.warnings and not cached.guidance)

    async def _run_research(
        self,
        *,
        planning: PlanningState,
        selected_evidence: ResearchEvidence | None,
    ) -> PlanningResearch:
        if selected_evidence is not None:
            return await self._service.research(
                planning.requirements, preselected_flight=selected_evidence
            )
        if planning.nearby_flight_dates_checked:
            return await self._service.research(
                planning.requirements, allow_nearby_flight_dates=False
            )
        return await self._service.research(planning.requirements)

    @staticmethod
    def _guidance_result(research: PlanningResearch) -> ResearchWorkflowResult:
        return {
            "research": research,
            "route": "respond",
            "assistant_response": "\n".join(
                reply.content for reply in research.guidance
            ),
            "clarification": next(
                (
                    reply.clarification
                    for reply in research.guidance
                    if reply.clarification
                ),
                None,
            ),
        }

    def _resolve_flight_selection(
        self,
        *,
        planning: PlanningState,
        research: PlanningResearch,
        cache_key: str,
    ) -> SelectionResult:
        if planning.requirements.transport != "flight":
            return SelectionResult(planning.selected_flight_id, None)

        decision = _resolve_selection(
            kind="flight", research=research, selected_id=planning.selected_flight_id
        )
        nearby_options = flight_date_options(research)
        if (
            decision.selected_id is None
            and not travel_options(research, "flight")
            and nearby_options
        ):
            pending = PendingTravelSelection(
                kind="flight_dates",
                option_ids=tuple(option_id for option_id, _ in nearby_options),
                reason="choose",
            )
            updated = planning.model_copy(
                update={
                    "research": research.model_dump(mode="json"),
                    "research_key": cache_key,
                    "pending_travel_selection": pending,
                    "selected_flight_id": None,
                    "nearby_flight_dates_checked": True,
                }
            )
            return SelectionResult(
                None,
                {
                    "research": research,
                    "route": "respond",
                    "planning": updated,
                    "assistant_response": flight_date_selection_text(
                        nearby_options, language=planning.language
                    ),
                },
            )
        if decision.pending is None:
            return SelectionResult(decision.selected_id, None)

        nearby_dates_checked = bool(nearby_options) or any(
            "nearby" in warning or "one-day-shifted" in warning
            for warning in research.warnings
            if warning.startswith("flights:")
        )
        updated = planning.model_copy(
            update={
                "research": research.model_dump(mode="json"),
                "research_key": cache_key,
                "pending_travel_selection": decision.pending,
                "selected_flight_id": decision.selected_id,
                "nearby_flight_dates_checked": nearby_dates_checked,
            }
        )
        return SelectionResult(
            decision.selected_id,
            {
                "research": research,
                "route": "respond",
                "planning": updated,
                "assistant_response": selection_text(
                    kind="flight",
                    options=travel_options(research, "flight"),
                    language=planning.language,
                    no_results=decision.pending.reason == "no_results",
                    nearby_search_note=self._nearby_search_note(research),
                ),
            },
        )

    @staticmethod
    def _nearby_search_note(
        research: PlanningResearch,
    ) -> Literal["incomplete", "checked"] | None:
        if any("nearby-date checks failed" in warning for warning in research.warnings):
            return "incomplete"
        if any(
            "one-day-shifted trip dates" in warning or "already checked" in warning
            for warning in research.warnings
        ):
            return "checked"
        return None

    @staticmethod
    def _resolve_hotel_selection(
        *,
        planning: PlanningState,
        research: PlanningResearch,
        cache_key: str,
        selected_flight_id: str | None,
    ) -> SelectionResult:
        if not planning.requirements.needs_lodging:
            return SelectionResult(planning.selected_hotel_id, None)
        decision = _resolve_selection(
            kind="hotel", research=research, selected_id=planning.selected_hotel_id
        )
        if decision.pending is None:
            return SelectionResult(decision.selected_id, None)
        updated = planning.model_copy(
            update={
                "research": research.model_dump(mode="json"),
                "research_key": cache_key,
                "pending_travel_selection": decision.pending,
                "selected_flight_id": selected_flight_id,
                "selected_hotel_id": decision.selected_id,
            }
        )
        return SelectionResult(
            decision.selected_id,
            {
                "research": research,
                "route": "respond",
                "planning": updated,
                "assistant_response": selection_text(
                    kind="hotel",
                    options=travel_options(research, "hotel"),
                    language=planning.language,
                    no_results=decision.pending.reason == "no_results",
                ),
            },
        )

    @staticmethod
    def _synthesis_result(
        *,
        planning: PlanningState,
        research: PlanningResearch,
        cache_key: str,
        selected_flight_id: str | None,
        selected_hotel_id: str | None,
    ) -> ResearchWorkflowResult:
        selected = research_for_selection(
            research, flight_id=selected_flight_id, hotel_id=selected_hotel_id
        )
        return {
            "research": selected,
            "route": "synthesize",
            "planning": planning.model_copy(
                update={
                    "research_key": cache_key,
                    "research": selected.model_dump(mode="json"),
                    "pending_travel_selection": None,
                    "selected_flight_id": selected_flight_id,
                    "selected_hotel_id": selected_hotel_id,
                }
            ),
        }


def _resolve_selection(
    *,
    research: PlanningResearch,
    kind: TravelKind,
    selected_id: str | None,
) -> TravelSelectionDecision:
    """Resolve ordinary flight or hotel options deterministically."""
    options = travel_options(research, kind)
    available_ids = {option_id for option_id, _ in options}
    if selected_id in available_ids:
        return TravelSelectionDecision(selected_id, None)
    if len(options) > 1:
        return TravelSelectionDecision(
            selected_id=None,
            pending=PendingTravelSelection(
                kind=kind,
                option_ids=tuple(option_id for option_id, _ in options),
                reason="choose",
            ),
        )
    if len(options) == 1:
        return TravelSelectionDecision(options[0][0], None)
    if has_no_verified_options(research, kind):
        return TravelSelectionDecision(
            selected_id=None,
            pending=PendingTravelSelection(kind=kind, reason="no_results"),
        )
    return TravelSelectionDecision(None, None)
