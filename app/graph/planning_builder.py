"""Deterministic conversational planner with two bounded model stages."""

import logging
import re
from time import perf_counter
from typing import NotRequired
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.common.time import utc_now
from app.domain.clarifications import TravelClarification
from app.domain.destination_selection import (
    CountryRecognition,
    PendingDestinationSelection,
)
from app.domain.flights import FlightCabinClass
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.trip_requirements import TripRequirements, TripTransport
from app.domain.trip_rules import inclusive_day_count
from app.graph.planning.deals_workflow import (
    DealDiscoveryWorkflow,
    match_deal_selection,
)
from app.graph.planning.research_workflow import PlanningResearchWorkflow
from app.graph.planning.standalone_workflow import StandaloneSearchWorkflow
from app.graph.planning.structured_output import (
    StructuredOutputValidationError,
    structured_call,
)
from app.graph.planning.synthesis_workflow import TripSynthesisWorkflow
from app.graph.planning.travel_selection import (
    match_selection,
    selected_flight_date_pair,
)
from app.graph.planning_context import PlanningRuntimeContext
from app.graph.planning_destination_questions import (
    country_selection_text,
    destination_selection_text,
)
from app.graph.planning_prompts import REQUIREMENTS_PROMPT
from app.graph.planning_questions import clarification_text
from app.graph.planning_schemas import (
    MAX_PLANNING_DAYS,
    RequirementExtraction,
)
from app.graph.state import TravelGraphState
from app.graph.subgraphs.model_gateway import ModelGateway
from app.observability.metrics import record_metric
from app.services.deal_discovery_service import (
    DealDiscoveryService,
    DealDiscoverySnapshot,
)
from app.services.destination_resolution_factory import DestinationResolutionFactory
from app.services.planning_research_service import (
    PlanningResearch,
    PlanningResearchService,
)
from app.services.standalone_search_service import (
    HotelsRequest,
    StandaloneRequest,
    StandaloneSearchService,
)
from app.services.trip_requirements_policy import TripRequirementsPolicy

logger = logging.getLogger(__name__)


class PlanningGraphState(TravelGraphState):
    planning: PlanningState
    research: NotRequired[PlanningResearch]
    latest_message: str
    recent_conversation: NotRequired[list[dict[str, str]]]
    user_message_id: NotRequired[UUID]
    route: NotRequired[str]
    reset_trip: NotRequired[bool]
    requirements_changed: NotRequired[bool]
    planning_preferences: NotRequired[PlanningPreferences]
    standalone_request: NotRequired[StandaloneRequest]
    clarification: NotRequired[TravelClarification | None]


def merge_requirements(
    current: TripRequirements, updates: dict[str, object]
) -> TripRequirements:
    """Omitted fields survive. Explicit null clears a field; unknown keys fail."""
    unknown = updates.keys() - TripRequirements.model_fields.keys()
    if unknown:
        raise ValueError("Unknown requirement fields")
    merged = {**current.model_dump(mode="json"), **updates}
    exact_date_fields = {"start_date", "end_date"}
    window_date_fields = {"date_window_start", "date_window_end"}
    exact_supplied = any(updates.get(field) is not None for field in exact_date_fields)
    window_supplied = any(
        updates.get(field) is not None for field in window_date_fields
    )

    if exact_supplied and window_supplied:
        raise ValueError("cannot update exact dates and a flexible window together")
    if exact_supplied:
        merged["date_window_start"] = None
        merged["date_window_end"] = None
    elif window_supplied:
        merged["start_date"] = None
        merged["end_date"] = None
    for field in ("interests", "constraints"):
        if field in updates and updates[field] is None:
            merged[field] = []
    if "duration_days" in updates and "end_date" not in updates:
        merged["end_date"] = None
    elif "end_date" in updates and "duration_days" not in updates:
        merged["duration_days"] = None
    elif (
        "start_date" in updates
        and "end_date" not in updates
        and current.duration_days is not None
    ):
        merged["end_date"] = None
    if updates.get("budget_decision") in {"undecided", "no_limit"}:
        for field in ("total_budget", "budget_currency"):
            if field not in updates:
                merged[field] = None
    if "minor_count" in updates and updates["minor_count"] != current.minor_count:
        if "minor_ages" not in updates:
            merged["minor_ages"] = None
        if "infant_on_lap" not in updates:
            merged["infant_on_lap"] = None
    if "minor_ages" in updates and "infant_on_lap" not in updates:
        merged["infant_on_lap"] = None
    if updates.get("needs_lodging") is False and "rooms" not in updates:
        merged["rooms"] = None
    if updates.get("minor_count") == 0:
        if "minor_ages" not in updates:
            merged["minor_ages"] = None
        if "infant_on_lap" not in updates:
            merged["infant_on_lap"] = None
    return TripRequirements.model_validate(merged)


def route_planning_requirements(
    planning: PlanningState,
    *,
    reset_trip: bool = False,
    preferences: PlanningPreferences | None = None,
) -> dict[str, object]:
    """Route validated requirements without another extraction call."""
    if planning.pending_country_selection is not None:
        current = PlanningState.model_validate(
            {
                **planning.model_dump(),
                "phase": "collecting",
                "pending_fields": ("destination",),
            }
        )
        return {
            "planning": current,
            "reset_trip": reset_trip,
            "route": "respond",
            "assistant_response": country_selection_text(
                planning.pending_country_selection, planning.language
            ),
        }
    pending_destination = planning.pending_destination_selection
    if pending_destination is not None:
        current = PlanningState.model_validate(
            {
                **planning.model_dump(),
                "phase": "collecting",
                "pending_fields": ("destination",),
            }
        )
        return {
            "planning": current,
            "reset_trip": reset_trip,
            "route": "respond",
            "assistant_response": destination_selection_text(
                pending_destination, current.language
            ),
        }
    budget_tier = preferences.budget_tier if preferences else None
    missing = TripRequirementsPolicy.missing_fields(
        planning.requirements,
        today=utc_now().date(),
        budget_tier=budget_tier,
    )
    current = PlanningState.model_validate(
        {
            **planning.model_dump(),
            "phase": "collecting" if missing else "ready",
            "pending_fields": missing,
        }
    )
    result: dict[str, object] = {
        "planning": current,
        "reset_trip": reset_trip,
    }
    if missing:
        return {
            **result,
            "route": "respond",
            "assistant_response": clarification_text(
                missing,
                current.language,
            ),
        }
    requirements = current.requirements
    if requirements.date_window_start is not None:
        if (
            requirements.duration_days is not None
            and requirements.duration_days > MAX_PLANNING_DAYS
        ):
            return {
                **result,
                "route": "respond",
                "assistant_response": f"Please split this trip into plans of at most {MAX_PLANNING_DAYS} days.",
            }
        if requirements.transport == TripTransport.FLIGHT:
            if requirements.cabin_class != FlightCabinClass.ECONOMY:
                return {
                    **result,
                    "route": "respond",
                    "assistant_response": (
                        "Flexible-date deals do not verify your requested cabin class. "
                        "Please give exact dates so I can search matching flights."
                    ),
                }
            return {**result, "route": "deal_discovery"}
        return {
            **result,
            "route": "respond",
            "assistant_response": (
                "Please choose exact travel dates before I plan this trip."
            ),
        }
    days = inclusive_day_count(
        requirements.start_date,
        requirements.resolved_end_date,
    )
    if days > MAX_PLANNING_DAYS:
        return {
            **result,
            "route": "respond",
            "assistant_response": (
                "Please split this trip into plans of at most "
                f"{MAX_PLANNING_DAYS} days."
            ),
        }

    return {
        **result,
        "route": "research",
    }


def build_planning_graph(
    *,
    model_gateway: ModelGateway,
    research_service: PlanningResearchService,
    standalone_service: StandaloneSearchService | None = None,
    deal_discovery_service: DealDiscoveryService | None = None,
    destination_factory: DestinationResolutionFactory | None = None,
):
    """No callable tools are attached to extraction or synthesis models."""
    graph = StateGraph(PlanningGraphState, context_schema=PlanningRuntimeContext)
    synthesis_workflow = TripSynthesisWorkflow(model_gateway=model_gateway)
    deal_workflow = DealDiscoveryWorkflow(deal_discovery_service=deal_discovery_service)
    research_workflow = PlanningResearchWorkflow(research_service=research_service)
    standalone_workflow = StandaloneSearchWorkflow(
        standalone_service=standalone_service
    )

    async def route_destination_requirements(
        planning: PlanningState,
        *,
        reset_trip: bool = False,
        preferences: PlanningPreferences,
    ) -> dict[str, object]:
        """Resolve country inputs before ordinary questions or research."""
        changed = False
        if (
            destination_factory is not None
            and planning.requirements.destination is not None
            and planning.pending_destination_selection is None
            and planning.pending_country_selection is None
        ):
            async with destination_factory.open() as service:
                recognition, pending = await service.prepare_country(
                    query=planning.requirements.destination,
                    planning=planning,
                    preferences=preferences,
                    allow_typo=True,
                )
            if pending is not None or recognition.status == "ambiguous":
                planning = PlanningState.model_validate(
                    {
                        **planning.model_dump(),
                        "pending_destination_selection": pending,
                        "pending_country_selection": (
                            recognition if recognition.status == "ambiguous" else None
                        ),
                    }
                )
                changed = True
        result = route_planning_requirements(
            planning, reset_trip=reset_trip, preferences=preferences
        )
        if changed:
            result["requirements_changed"] = True
        return result

    async def destination_reply(
        state: PlanningGraphState, preferences: PlanningPreferences
    ) -> dict[str, object] | None:
        """Revalidate simple selections before model extraction can clear them."""
        previous = state["planning"]
        country_proposals = previous.pending_country_selection
        pending = previous.pending_destination_selection
        if destination_factory is None or (
            pending is None and country_proposals is None
        ):
            return None
        text = state["latest_message"].strip()
        number_match = re.fullmatch(
            r"(?:option|city|country)?\s*([0-9]{1,3})[.]?", text, re.I
        )
        index = int(number_match[1]) - 1 if number_match else None
        updated_pending = None
        selected = None
        country_confirmed = False
        async with destination_factory.open() as service:
            if country_proposals is not None:
                if index is not None:
                    choice = (
                        country_proposals.matches[index]
                        if 0 <= index < len(country_proposals.matches)
                        else None
                    )
                else:
                    choice = next(
                        (
                            match
                            for match in country_proposals.matches
                            if text.casefold()
                            in {
                                match.country_code.casefold(),
                                match.country_name.casefold(),
                            }
                        ),
                        None,
                    )
                if choice is None:
                    return (
                        route_planning_requirements(previous, preferences=preferences)
                        if number_match
                        else None
                    )
                updated_pending = await service.confirm_country(
                    recognition=country_proposals,
                    selected_code=choice.country_code,
                    planning=previous,
                    preferences=preferences,
                )
                country_confirmed = True
            elif pending is not None:
                if pending.country.requires_confirmation and text.casefold() in {
                    "yes",
                    "yes please",
                    "haan",
                    "han",
                    "ji",
                }:
                    updated_pending = await service.confirm_country(
                        recognition=CountryRecognition(
                            status="confirmation_required", matches=(pending.country,)
                        ),
                        selected_code=pending.country.country_code,
                        planning=previous,
                        preferences=preferences,
                    )
                    country_confirmed = True
                else:
                    if index is not None:
                        if not 0 <= index < len(pending.options):
                            return route_planning_requirements(
                                previous, preferences=preferences
                            )
                        selected = await service.select_offered_city(
                            pending=pending,
                            destination_id=pending.options[index].destination_id,
                        )
                        if selected is None:
                            updated_pending = PendingDestinationSelection(
                                country=pending.country,
                                options=tuple(
                                    option
                                    for offset, option in enumerate(pending.options)
                                    if offset != index
                                ),
                            )
                    elif 2 <= len(text) <= 120:
                        matches = await service.resolve_city_name(
                            pending=pending, query=text
                        )
                        if len(matches) == 1:
                            selected = matches[0]
                        elif len(matches) > 1:
                            updated_pending = PendingDestinationSelection(
                                country=pending.country, options=matches
                            )
                    if selected is None and updated_pending is None:
                        return None
        if selected is not None:
            updates: dict[str, object] = {"destination": selected.name}
            if (
                previous.transport_inferred
                and selected.name != previous.requirements.destination
            ):
                updates.update(transport=None, cabin_class=None)
            try:
                requirements = merge_requirements(previous.requirements, updates)
            except (ValueError, ValidationError):
                return route_planning_requirements(previous, preferences=preferences)
            current = PlanningState.model_validate(
                {
                    **previous.model_dump(),
                    "requirements": requirements,
                    "pending_destination_selection": None,
                    "pending_country_selection": None,
                    "transport_inferred": (
                        False if "transport" in updates else previous.transport_inferred
                    ),
                    "revision": previous.revision + 1,
                    "itinerary": None,
                    "research": None,
                    "research_key": None,
                    "pending_travel_selection": None,
                    "selected_deal": None,
                    "deal_discovery": None,
                    "selected_flight_id": None,
                    "selected_hotel_id": None,
                    "nearby_flight_dates_checked": False,
                }
            )
        else:
            requirements = previous.requirements
            if updated_pending is not None and country_confirmed:
                requirements = merge_requirements(
                    requirements, {"destination": updated_pending.country.country_name}
                )
            current = PlanningState.model_validate(
                {
                    **previous.model_dump(),
                    "requirements": requirements,
                    "pending_country_selection": None,
                    "pending_destination_selection": updated_pending,
                    "revision": previous.revision + 1,
                }
            )
        return {
            **route_planning_requirements(current, preferences=preferences),
            "requirements_changed": True,
        }

    async def extract(state: PlanningGraphState) -> dict[str, object]:
        default_updates: dict[str, object] = {}
        preferences = state.get(
            "planning_preferences",
            PlanningPreferences(),
        )
        previous = state["planning"]
        message_id = state.get("user_message_id")
        if message_id is not None and previous.requirements_message_id == message_id:
            return await route_destination_requirements(
                previous, preferences=preferences
            )
        destination_update = await destination_reply(state, preferences)
        if destination_update is not None:
            return destination_update
        if (
            previous.pending_travel_selection
            and previous.pending_travel_selection.kind == "deal"
        ):
            if previous.deal_discovery is not None:
                snapshot = DealDiscoverySnapshot.model_validate(previous.deal_discovery)
                choice = match_deal_selection(state["latest_message"], snapshot)
                if (
                    choice is not None
                    and choice.option_id in previous.pending_travel_selection.option_ids
                ):
                    request = snapshot.request
                    request_updates = (
                        {
                            "origin": request.origin,
                            "destination": request.destination,
                            "adults": request.adults,
                            "minor_count": (
                                request.children
                                + request.infants_in_seat
                                + request.infants_on_lap
                            ),
                            "transport": "flight",
                            "cabin_class": "economy",
                        }
                        if request is not None
                        else {}
                    )
                    requirements = merge_requirements(
                        previous.requirements,
                        {
                            **request_updates,
                            "start_date": choice.deal.start_date.isoformat(),
                            "end_date": choice.deal.end_date.isoformat(),
                            "duration_days": choice.deal.duration_days,
                        },
                    )
                    updated = PlanningState.model_validate(
                        {
                            **previous.model_dump(),
                            "requirements": requirements,
                            "revision": previous.revision + 1,
                            "itinerary": None,
                            "research": None,
                            "research_key": None,
                            "pending_travel_selection": None,
                            "selected_deal": choice.model_dump(mode="json"),
                            "selected_flight_id": choice.option_id,
                            "selected_hotel_id": None,
                        }
                    )
                    return {
                        **await route_destination_requirements(
                            updated, preferences=preferences
                        ),
                        "requirements_changed": True,
                    }
        if previous.pending_travel_selection and previous.research:
            stored_research = PlanningResearch.model_validate(previous.research)
            selected_id = match_selection(
                state["latest_message"],
                previous.pending_travel_selection,
                stored_research,
            )
            if selected_id is not None:
                if previous.pending_travel_selection.kind == "flight_dates":
                    selected_dates = selected_flight_date_pair(
                        stored_research, selected_id
                    )
                    if selected_dates is None:
                        return {
                            "route": "respond",
                            "planning": previous,
                            "assistant_response": (
                                "That nearby-date option is no longer available. Please send your preferred travel dates."
                            ),
                        }
                    departure, returning = selected_dates
                    requirements = merge_requirements(
                        previous.requirements,
                        {
                            "start_date": departure.isoformat(),
                            "end_date": returning.isoformat(),
                            "duration_days": inclusive_day_count(departure, returning),
                        },
                    )
                    updated = PlanningState(
                        **{
                            **previous.model_dump(),
                            "requirements": requirements,
                            "revision": previous.revision + 1,
                            "phase": "ready",
                            "pending_fields": (),
                            "itinerary": None,
                            "research": None,
                            "research_key": None,
                            "pending_travel_selection": None,
                            "selected_flight_id": None,
                            "selected_hotel_id": None,
                        }
                    )
                    return {
                        **await route_destination_requirements(
                            updated,
                            preferences=preferences,
                        ),
                        "requirements_changed": True,
                    }
                selection_update = {
                    "pending_travel_selection": None,
                    "selected_flight_id": (
                        selected_id
                        if previous.pending_travel_selection.kind == "flight"
                        else previous.selected_flight_id
                    ),
                    "selected_hotel_id": (
                        selected_id
                        if previous.pending_travel_selection.kind == "hotel"
                        else previous.selected_hotel_id
                    ),
                }
                return await route_destination_requirements(
                    previous.model_copy(update=selection_update),
                    preferences=preferences,
                )
        extraction_prompt = REQUIREMENTS_PROMPT
        extraction_data = {
            "today": utc_now().date().isoformat(),
            "profile_preferences": preferences.model_dump(mode="json"),
            "state": previous.model_dump(
                mode="json",
                exclude={"itinerary", "research", "deal_discovery", "selected_deal"},
            ),
            "recent_conversation": state.get("recent_conversation", []),
            "message": state["latest_message"],
        }
        extraction = None
        validation_attempts: list[dict[str, object]] = []
        for attempt in range(2):
            try:
                extraction = await structured_call(
                    model_gateway,
                    RequirementExtraction,
                    prompt=(
                        extraction_prompt
                        if attempt == 0
                        else extraction_prompt
                        + " The previous JSON did not match the schema. Correct it, "
                        "return only valid JSON, and keep reply under 300 characters."
                    ),
                    data=extraction_data,
                )
                break
            except StructuredOutputValidationError as error:
                validation_attempts.append(
                    {
                        "attempt": attempt + 1,
                        "error_count": error.error_count,
                        "error_types": error.error_types,
                        "errors": error.error_details,
                    }
                )
                repair_instructions = {
                    "extraction_search_mismatch": (
                        "When intent='search', provide exactly one supported typed search "
                        "request. For every other intent, omit search or set it to null."
                    ),
                    "extraction_duplicate_changed_fields": (
                        "Remove duplicate names from changed_fields. List each explicitly "
                        "changed field exactly once."
                    ),
                    "extraction_missing_update": (
                        "Include an updates property for every name in changed_fields. "
                        "Use null only when the user explicitly clears that field. "
                        "Remove names that the user did not change."
                    ),
                }
                extraction_data["repair"] = {
                    "fields": error.error_fields,
                    "errors": error.error_types,
                    "details": error.error_details,
                    "instructions": [
                        repair_instructions[error_type]
                        for error_type in error.error_types
                        if error_type in repair_instructions
                    ],
                }
                if attempt == 0:
                    continue
                logger.warning(
                    "Requirement extraction returned invalid structured output",
                    extra={
                        "schema_name": error.schema_name,
                        "validation_error_count": error.error_count,
                        "validation_error_types": error.error_types,
                        "validation_error_fields": error.error_fields,
                        "validation_errors": error.error_details,
                        "validation_attempts": validation_attempts,
                        "attempts": attempt + 1,
                    },
                )
                raise
        assert extraction is not None
        if extraction.intent == "search":
            if (
                isinstance(extraction.search, HotelsRequest)
                and extraction.search.budget_decision is None
                and extraction.search.arguments.rooms == 1
            ):
                question = (
                    "What is your maximum budget for the entire hotel stay, and "
                    "which currency? You can also say no limit."
                    if extraction.language == "en"
                    else "Poore hotel stay ka maximum budget aur currency kya hai? "
                    "Agar limit nahi rakhni to ‘no limit’ keh dein."
                )
                planning = previous.model_copy(
                    update={
                        "language": extraction.language,
                        "pending_search": extraction.search.model_dump(mode="json"),
                    }
                )
                return {
                    "route": "respond",
                    "assistant_response": question,
                    "planning": planning,
                }
            return {"route": "standalone", "standalone_request": extraction.search}
        if extraction.intent == "chat":
            return {
                "route": "respond",
                "assistant_response": extraction.reply
                or "How can I help with your trip?",
            }
        base = (
            PlanningState(language=extraction.language)
            if extraction.intent == "new_trip"
            else previous
        )
        if (
            not base.interests_overridden
            and not base.requirements.interests
            and preferences.interests
        ):
            default_updates["interests"] = [
                interest.value for interest in preferences.interests
            ]
        extracted_updates = extraction.selected_updates()
        updates = {**default_updates, **extracted_updates}
        verified_city = False
        replacement_pending = None
        proposed_destination = updates.get("destination")
        if (
            destination_factory is None
            and isinstance(proposed_destination, str)
            and proposed_destination != base.requirements.destination
            and (base.pending_destination_selection or base.pending_country_selection)
        ):
            # A direct caller without the resolver must fail closed rather than
            # accept an unverified city from model output.
            updates.pop("destination", None)
            extracted_updates.pop("destination", None)
        if (
            destination_factory is not None
            and isinstance(proposed_destination, str)
            and proposed_destination != base.requirements.destination
            and (base.pending_destination_selection or base.pending_country_selection)
        ):
            async with destination_factory.open() as service:
                recognized = service.country_recognition.recognize(proposed_destination)
                # Explicit country changes restart country-to-city collection.
                if recognized.status != "exact":
                    matches = (
                        await service.resolve_city_name(
                            pending=base.pending_destination_selection,
                            query=proposed_destination,
                        )
                        if base.pending_destination_selection is not None
                        else ()
                    )
                    if len(matches) == 1:
                        updates["destination"] = matches[0].name
                        extracted_updates["destination"] = matches[0].name
                        verified_city = True
                    else:
                        # Retain unrelated validated updates, but never let an
                        # unverified city erase country/selection context.
                        updates.pop("destination", None)
                        extracted_updates.pop("destination", None)
                        if matches and base.pending_destination_selection is not None:
                            replacement_pending = PendingDestinationSelection(
                                country=base.pending_destination_selection.country,
                                options=matches,
                            )
        changed_flight_fields = {
            "origin",
            "destination",
            "start_date",
            "end_date",
            "date_window_start",
            "date_window_end",
            "duration_days",
            "adults",
            "minor_count",
            "minor_ages",
            "infant_on_lap",
            "transport",
            "cabin_class",
            "budget_currency",
        }
        changed_hotel_fields = {
            "destination",
            "start_date",
            "end_date",
            "date_window_start",
            "date_window_end",
            "duration_days",
            "adults",
            "minor_count",
            "minor_ages",
            "needs_lodging",
            "rooms",
            "budget_decision",
            "total_budget",
            "budget_currency",
        }
        clear_flight = bool(changed_flight_fields.intersection(extracted_updates))
        clear_hotel = bool(changed_hotel_fields.intersection(extracted_updates))
        pending_selection = base.pending_travel_selection
        if pending_selection and (
            (pending_selection.kind == "flight" and clear_flight)
            or (pending_selection.kind == "hotel" and clear_hotel)
            or (pending_selection.kind == "deal" and clear_flight)
        ):
            pending_selection = None
        route_changed = any(
            field in extracted_updates
            and extracted_updates[field] != getattr(base.requirements, field)
            for field in ("origin", "destination")
        )
        if (
            route_changed
            and base.transport_inferred
            and "transport" not in extracted_updates
        ):
            updates["transport"] = None
            updates["cabin_class"] = None
        transport_inferred = base.transport_inferred
        if "transport" in updates:
            transport_inferred = (
                updates["transport"] == "flight" and extraction.transport_inferred
            )
        try:
            requirements = merge_requirements(base.requirements, updates)
        except (ValueError, ValidationError):
            return {
                "route": "respond",
                "assistant_response": (
                    "Trip ki kuch details aapas mein match nahi kar raheen. Barah-e-karam sirf ghalat detail durust kar dein."
                    if extraction.language == "ur-Latn"
                    else "Some trip details conflict. Please correct only the detail that is wrong."
                ),
            }
        pending_destination = base.pending_destination_selection
        pending_country = base.pending_country_selection

        destination_changed = requirements.destination != base.requirements.destination
        interests_changed = requirements.interests != base.requirements.interests

        if destination_changed or (
            interests_changed and destination_factory is not None
        ):
            pending_destination = None
        if destination_changed:
            pending_country = None
        if replacement_pending is not None:
            pending_destination = replacement_pending
        planning = PlanningState(
            requirements=requirements,
            transport_inferred=transport_inferred,
            language=extraction.language,
            revision=previous.revision + 1,
            last_turn_number=previous.last_turn_number,
            interests_overridden=base.interests_overridden
            or "interests" in extracted_updates,
            context_start_message_id=(
                message_id
                if extraction.intent == "new_trip"
                else base.context_start_message_id or message_id
            ),
            itinerary=base.itinerary,
            research=base.research,
            research_key=base.research_key,
            pending_travel_selection=pending_selection,
            pending_destination_selection=pending_destination,
            pending_country_selection=pending_country,
            deal_discovery=base.deal_discovery,
            selected_deal=None if clear_flight else base.selected_deal,
            selected_flight_id=None if clear_flight else base.selected_flight_id,
            selected_hotel_id=None if clear_hotel else base.selected_hotel_id,
            nearby_flight_dates_checked=(
                False if clear_flight else base.nearby_flight_dates_checked
            ),
        )
        result = (
            route_planning_requirements(
                planning,
                reset_trip=extraction.intent == "new_trip",
                preferences=preferences,
            )
            if verified_city
            else await route_destination_requirements(
                planning,
                reset_trip=extraction.intent == "new_trip",
                preferences=preferences,
            )
        )
        return {**result, "requirements_changed": True}

    async def persist_requirements(
        state: PlanningGraphState,
        runtime: Runtime[PlanningRuntimeContext],
    ) -> dict[str, object]:
        if not state.get("requirements_changed", False):
            return {}
        if runtime.context is None:
            raise RuntimeError("Planning requirements persistence context is required")
        started = perf_counter()
        outcome = "error"
        try:
            persisted = await runtime.context.persist_requirements(
                state["planning"], state.get("reset_trip", False)
            )
            outcome = "success"
        finally:
            record_metric(
                name="planning_requirements_save_duration_ms",
                value=(perf_counter() - started) * 1000,
                metric_type="distribution",
                labels={"outcome": outcome},
            )

        return {
            "planning": persisted,
            "requirements_changed": False,
        }

    async def discover_deals(state: PlanningGraphState) -> dict[str, object]:
        return await deal_workflow.run(planning=state["planning"])

    async def research(state: PlanningGraphState) -> dict[str, object]:
        return await research_workflow.run(planning=state["planning"])

    async def standalone(state: PlanningGraphState) -> dict[str, object]:
        return await standalone_workflow.run(
            request=state["standalone_request"], planning=state["planning"]
        )

    async def synthesize(state: PlanningGraphState) -> dict[str, object]:
        return await synthesis_workflow.run(
            planning=state["planning"],
            research=state["research"],
            latest_message=state["latest_message"],
            preferences=state.get(
                "planning_preferences",
                PlanningPreferences(),
            ),
        )

    graph.add_node("extract_requirements", extract)
    graph.add_node("persist_requirements", persist_requirements)
    graph.add_node("research", research)
    graph.add_node("synthesize_itinerary", synthesize)
    graph.add_node("standalone_search", standalone)
    graph.add_node("deal_discovery", discover_deals)

    graph.add_edge(START, "extract_requirements")
    graph.add_edge("extract_requirements", "persist_requirements")
    graph.add_conditional_edges(
        "persist_requirements",
        lambda state: state["route"],
        {
            "respond": END,
            "research": "research",
            "standalone": "standalone_search",
            "deal_discovery": "deal_discovery",
        },
    )
    graph.add_conditional_edges(
        "research",
        lambda state: state["route"],
        {"respond": END, "synthesize": "synthesize_itinerary"},
    )
    graph.add_edge("synthesize_itinerary", END)
    graph.add_edge("standalone_search", END)
    graph.add_edge("deal_discovery", END)
    return graph.compile()
