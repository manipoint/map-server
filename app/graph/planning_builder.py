"""Deterministic conversational planner with two bounded model stages."""

import json
import logging
from datetime import timedelta
from time import perf_counter
from typing import NotRequired, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError

from app.common.time import utc_now
from app.domain.clarifications import TravelClarification
from app.domain.itineraries import ItineraryItemType
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.trip_requirements import TripRequirements
from app.domain.trip_rules import inclusive_day_count
from app.graph.model_response import ModelResponseRefusedError, model_response_text
from app.graph.planning_context import PlanningRuntimeContext
from app.graph.planning_prompts import (
    REQUIREMENTS_PROMPT,
    REQUIREMENTS_PROMPT_VERSION,
    SYNTHESIS_PROMPT,
    SYNTHESIS_PROMPT_VERSION,
)
from app.graph.planning_questions import clarification_text
from app.graph.planning_schemas import (
    MAX_PLANNING_DAYS,
    MAX_STRUCTURED_RESPONSE_CHARS,
    RequirementExtraction,
    ResearchedItinerary,
)
from app.graph.schemas.itineraries import GeneratedItinerary
from app.graph.state import TravelGraphState
from app.graph.subgraphs.model_gateway import ModelGateway
from app.observability.metrics import record_metric
from app.services.planning_research_service import (
    PlanningResearch,
    PlanningResearchService,
)
from app.services.standalone_search_service import (
    StandaloneRequest,
    StandaloneSearchService,
)
from app.services.trip_requirements_policy import TripRequirementsPolicy

Output = TypeVar("Output", bound=BaseModel)
logger = logging.getLogger(__name__)


class StructuredOutputValidationError(ValueError):
    """A model response failed its expected structured-output schema."""

    def __init__(
        self,
        *,
        schema_name: str,
        validation_error: ValidationError,
        allowed_fields: set[str],
    ) -> None:
        errors = validation_error.errors(
            include_url=False, include_context=False, include_input=False
        )
        error_types = sorted(
            {item["type"] for item in errors if isinstance(item.get("type"), str)}
        )
        error_fields = sorted(
            {
                location[0]
                for item in errors
                if (location := item.get("loc"))
                and isinstance(location[0], str)
                and location[0] in allowed_fields
            }
        )
        self.schema_name = schema_name
        self.error_count = len(errors)
        self.error_types = tuple(error_types[:5])
        self.error_fields = tuple(error_fields[:5])
        super().__init__(f"Model response does not match {schema_name}.")


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


async def structured_call(
    gateway: ModelGateway, schema: type[Output], *, prompt: str, data: dict[str, object]
) -> Output:
    started = perf_counter()
    response = await gateway.generate(
        schema=schema,
        messages=[
            SystemMessage(content=prompt),
            HumanMessage(
                content=json.dumps(
                    data,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
        ],
    )
    stage = "requirements" if schema is RequirementExtraction else "synthesis"
    record_metric(
        name="planning_model_duration_ms",
        value=(perf_counter() - started) * 1000,
        metric_type="distribution",
        labels={
            "stage": stage,
            "prompt_version": REQUIREMENTS_PROMPT_VERSION
            if stage == "requirements"
            else SYNTHESIS_PROMPT_VERSION,
        },
    )
    for key in ("input_tokens", "output_tokens"):
        count = (response.usage_metadata or {}).get(key)
        if isinstance(count, int):
            record_metric(
                name="planning_model_tokens",
                value=count,
                metric_type="counter",
                labels={"stage": stage, "direction": key},
            )
    if response.tool_calls:
        raise ValueError("Expected tool-free structured model output")
    content = model_response_text(response)
    if len(content) > MAX_STRUCTURED_RESPONSE_CHARS:
        raise ValueError("Structured model output exceeds the size limit")
    try:
        return schema.model_validate_json(content)

    except ValidationError as error:
        raise StructuredOutputValidationError(
            schema_name=schema.__name__,
            validation_error=error,
            allowed_fields=set(schema.model_fields),
        ) from error


def merge_requirements(
    current: TripRequirements, updates: dict[str, object]
) -> TripRequirements:
    """Omitted fields survive. Explicit null clears a field; unknown keys fail."""
    unknown = updates.keys() - TripRequirements.model_fields.keys()
    if unknown:
        raise ValueError("Unknown requirement fields")
    merged = {**current.model_dump(mode="json"), **updates}
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


def validate_researched_itinerary(
    generated: ResearchedItinerary,
    *,
    requirements: TripRequirements,
    research: PlanningResearch,
    language: str = "en",
) -> GeneratedItinerary:
    start = requirements.start_date
    end = requirements.resolved_end_date
    if start is None or end is None:
        raise ValueError("Itinerary requires complete dates")
    days = inclusive_day_count(start, end)
    if {item.day_number for item in generated.items} != set(range(1, days + 1)):
        raise ValueError(
            "Itinerary must cover every requested day exactly by day number"
        )
    evidence = {item.id: item for item in research.evidence}
    previous_end = None
    previous_day = 0
    normalized = []
    for item in generated.items:
        if item.day_number < previous_day:
            raise ValueError("Itinerary items must be ordered by day")
        previous_day = item.day_number
        expected_date = start + timedelta(days=item.day_number - 1)
        if (item.starts_at is None) != (item.ends_at is None):
            raise ValueError("Scheduled items require both start and end times")
        values = item.model_dump(exclude={"evidence_id", "generic_activity"})
        # A model can select evidence but cannot supply its own image URLs.
        values["image"] = None
        values["start_time_zone"] = research.time_zone
        values["end_time_zone"] = research.time_zone
        if item.evidence_id is not None:
            source = evidence.get(item.evidence_id)
            if source is None or (source.expires_at and source.expires_at <= utc_now()):
                raise ValueError("Itinerary references missing or expired evidence")
            if item.item_type.value != source.kind:
                raise ValueError("Itinerary evidence type does not match item type")
            # Display names come from trusted normalized results, never the model.
            values.update(
                title=source.name,
                location_name=source.location,
                description=source.description,
                starts_at=source.starts_at
                if source.kind == "flight"
                else item.starts_at,
                ends_at=source.ends_at if source.kind == "flight" else item.ends_at,
                start_time_zone=source.start_time_zone
                if source.kind == "flight"
                else research.time_zone,
                end_time_zone=source.end_time_zone
                if source.kind == "flight"
                else research.time_zone,
                image=source.image
                or (source.hotel_card.image if source.hotel_card else None),
            )
        elif item.item_type in {
            ItineraryItemType.PLACE,
            ItineraryItemType.HOTEL,
            ItineraryItemType.FLIGHT,
        }:
            raise ValueError("Named place, hotel and flight items require evidence IDs")
        else:
            # Free text cannot bypass evidence requirements by choosing 'activity'.
            templates = {
                "explore": (
                    "Explore the area",
                    "Choose a local activity after checking access and opening hours.",
                ),
                "walk": (
                    "Optional walk",
                    "Choose an accessible route suitable for your group.",
                ),
                "meal": (
                    "Meal break",
                    "Choose a venue that meets your dietary requirements.",
                ),
                "transfer": (
                    "Transfer",
                    "Arrange transport; no reservation has been made.",
                ),
                "rest": ("Rest break", "Unscheduled time to rest."),
                "free_time": ("Free time", "Choose an activity that suits your group."),
                "note": (
                    "Planning reminder",
                    "Check availability, access and costs before travel.",
                ),
            }
            if language == "ur-Latn":
                templates = {
                    "explore": (
                        "Ilaqa dekhein",
                        "Rasai aur khulne ke auqat tasdeeq kar ke maqami sargarmi chunain.",
                    ),
                    "walk": (
                        "Ikhtiyari sair",
                        "Apne group ki zarooriyat ke mutabiq qabil-e-rasai rasta chunain.",
                    ),
                    "meal": (
                        "Khanay ka waqfa",
                        "Apni ghizai zarooriyat ke mutabiq jagah chunain.",
                    ),
                    "transfer": (
                        "Safar ka intizam",
                        "Transport ka intizam karein; koi booking nahi hui.",
                    ),
                    "rest": ("Aram ka waqfa", "Aram ke liye waqt."),
                    "free_time": (
                        "Khali waqt",
                        "Apne group ke mutabiq sargarmi chunain.",
                    ),
                    "note": (
                        "Yad dehani",
                        "Safar se pehle dastiyabi, rasai aur kharch tasdeeq karein.",
                    ),
                }
            code = item.generic_activity or {
                "meal": "meal",
                "transfer": "transfer",
                "note": "note",
            }.get(item.item_type.value, "free_time")
            values.update(
                title=templates[code][0],
                description=templates[code][1],
                location_name=None,
            )
        starts, ends = values["starts_at"], values["ends_at"]
        if item.item_type != ItineraryItemType.FLIGHT:
            if research.time_zone is None:
                values.update(starts_at=None, ends_at=None)
                starts = ends = None
            elif starts is not None:
                try:
                    zone = ZoneInfo(research.time_zone)
                except ZoneInfoNotFoundError as error:
                    raise ValueError("Research time zone is unavailable") from error
                for value in (starts, ends):
                    if value.utcoffset() != value.astimezone(zone).utcoffset():
                        raise ValueError(
                            "Scheduled offset does not match destination time zone"
                        )
                if ends.date() != expected_date:
                    raise ValueError("Activity must finish on its trip day")
        if starts is not None:
            if starts.date() != expected_date or ends is None or ends <= starts:
                raise ValueError(
                    "Scheduled item must match its trip day and time order"
                )
            if previous_end is not None and starts < previous_end:
                raise ValueError(
                    "Scheduled itinerary items overlap or are out of order"
                )
            previous_end = ends
        normalized.append(values)
    return GeneratedItinerary(
        summary="Aap ki safar ki zarooriyat par mabni draft plan."
        if language == "ur-Latn"
        else "Draft itinerary based on your trip requirements.",
        items=normalized,
    )


def route_planning_requirements(
    planning: PlanningState,
    *,
    reset_trip: bool = False,
    preferences: PlanningPreferences | None = None,
) -> dict[str, object]:
    """Route validated requirements without another extraction call."""
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
):
    """No callable tools are attached to extraction or synthesis models."""
    graph = StateGraph(PlanningGraphState, context_schema=PlanningRuntimeContext)

    async def extract(state: PlanningGraphState) -> dict[str, object]:
        default_updates: dict[str, object] = {}
        preferences = state.get(
            "planning_preferences",
            PlanningPreferences(),
        )
        previous = state["planning"]
        message_id = state.get("user_message_id")
        if message_id is not None and previous.requirements_message_id == message_id:
            return route_planning_requirements(previous, preferences=preferences)
        extraction_prompt = REQUIREMENTS_PROMPT
        extraction_data = {
            "today": utc_now().date().isoformat(),
            "profile_preferences": preferences.model_dump(mode="json"),
            "state": previous.model_dump(
                mode="json", exclude={"itinerary", "research"}
            ),
            "recent_conversation": state.get("recent_conversation", []),
            "message": state["latest_message"],
        }
        extraction = None
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
                extraction_data["repair"] = {
                    "fields": error.error_fields,
                    "errors": error.error_types,
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
                        "attempts": attempt + 1,
                    },
                )
                raise
        assert extraction is not None
        if extraction.intent == "search":
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
        )
        return {
            **route_planning_requirements(
                planning,
                reset_trip=extraction.intent == "new_trip",
                preferences=preferences,
            ),
            "requirements_changed": True,
        }

    async def persist_requirements(
        state: PlanningGraphState,
        runtime: Runtime[PlanningRuntimeContext],
    ) -> dict[str, object]:
        if not state.get("requirements_changed", False):
            return {}
        if runtime.context is None:
            raise RuntimeError("Planning requirements persistence context is required")
        persisted = await runtime.context.persist_requirements(
            state["planning"], state.get("reset_trip", False)
        )
        return {
            "planning": persisted,
            "requirements_changed": False,
        }

    async def research(state: PlanningGraphState) -> dict[str, object]:
        planning = state["planning"]
        key = research_service.cache_key(planning.requirements)
        cached = (
            PlanningResearch.model_validate(planning.research)
            if planning.research
            else None
        )
        now = utc_now()
        reusable = (
            cached is not None
            and planning.research_key == key
            and timedelta(0) <= now - cached.searched_at < timedelta(minutes=5)
            and not cached.warnings
            and not cached.guidance
            and all(
                item.expires_at is None or item.expires_at > now
                for item in cached.evidence
            )
        )
        result = (
            cached
            if reusable
            else await research_service.research(planning.requirements)
        )
        if result.guidance:
            return {
                "research": result,
                "route": "respond",
                "assistant_response": "\\n".join(
                    reply.content for reply in result.guidance
                ),
                "clarification": next(
                    (
                        reply.clarification
                        for reply in result.guidance
                        if reply.clarification
                    ),
                    None,
                ),
            }
        return {
            "research": result,
            "route": "synthesize",
            "planning": planning.model_copy(update={"research_key": key}),
        }

    async def standalone(state: PlanningGraphState) -> dict[str, object]:
        if standalone_service is None:
            return {"assistant_response": "Live standalone search is unavailable."}
        request = state["standalone_request"]
        reply = await standalone_service.search(request)
        planning = state["planning"].model_copy(
            update={
                "pending_search": request.model_dump(mode="json")
                if reply.input_required
                else None,
            }
        )
        return {
            "assistant_response": reply.content,
            "clarification": reply.clarification,
            "planning": planning,
        }

    async def synthesize(state: PlanningGraphState) -> dict[str, object]:
        planning = state["planning"]
        research = state["research"]
        prompt = SYNTHESIS_PROMPT
        data = {
            "requirements": planning.requirements.model_dump(mode="json"),
            "language": planning.language,
            "research": research.model_dump(mode="json"),
            "previous_itinerary": planning.itinerary,
            "request": state["latest_message"],
            "profile_preferences": state.get(
                "planning_preferences",
                PlanningPreferences(),
            ).model_dump(mode="json", exclude={"home_city", "home_country_code"}),
        }
        for attempt in range(2):
            try:
                proposed = await structured_call(
                    model_gateway, ResearchedItinerary, prompt=prompt, data=data
                )
                generated = validate_researched_itinerary(
                    proposed,
                    requirements=planning.requirements,
                    research=research,
                    language=planning.language,
                )
                break
            except ModelResponseRefusedError:
                raise
            except ValueError as error:
                if attempt:
                    raise
                # No raw malformed output or validation input is replayed or logged.
                data["repair"] = (
                    "Previous output failed validation. Rebuild using the exact schema, all days, valid evidence IDs and non-overlapping times."
                )
                data["validation_failure"] = (
                    {"fields": error.error_fields, "errors": error.error_types}
                    if isinstance(error, StructuredOutputValidationError)
                    else str(error)[:200]
                )
        text = generated.summary + (
            "\n\nYe draft plan hai. Availability aur total cost confirm karna baqi hai."
            if planning.language == "ur-Latn"
            else "\n\nThis is a draft. Availability and total cost still need confirmation."
        )
        if research.warnings:
            text += "\n\n" + "\n".join(research.warnings)
        return {
            "generated_itinerary": generated,
            "assistant_response": text,
            "planning": PlanningState(
                **{
                    **planning.model_dump(),
                    "phase": "generated",
                    "itinerary": generated.model_dump(mode="json"),
                    "research": research.model_dump(mode="json"),
                }
            ),
        }

    graph.add_node("extract_requirements", extract)
    graph.add_node("persist_requirements", persist_requirements)
    graph.add_node("research", research)
    graph.add_node("synthesize_itinerary", synthesize)
    graph.add_node("standalone_search", standalone)

    graph.add_edge(START, "extract_requirements")
    graph.add_edge("extract_requirements", "persist_requirements")
    graph.add_conditional_edges(
        "persist_requirements",
        lambda state: state["route"],
        {"respond": END, "research": "research", "standalone": "standalone_search"},
    )
    graph.add_conditional_edges(
        "research",
        lambda state: state["route"],
        {"respond": END, "synthesize": "synthesize_itinerary"},
    )
    graph.add_edge("synthesize_itinerary", END)
    graph.add_edge("standalone_search", END)
    return graph.compile()
