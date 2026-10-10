"""Deterministic conversational planner with two bounded model stages."""

import hashlib
import json
import logging
import re
from datetime import date, timedelta
from time import perf_counter
from typing import NotRequired, TypeVar, get_args
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError

from app.common.time import utc_now
from app.domain.clarifications import TravelClarification
from app.domain.flights import FlightCabinClass
from app.domain.itineraries import MAX_ITINERARY_ITEMS, ItineraryItemType
from app.domain.planning import PendingTravelSelection, PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.trip_requirements import TripRequirements, TripTransport
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
from app.services.deal_discovery_service import (
    DealChoice,
    DealDiscoveryInput,
    DealDiscoveryService,
    DealDiscoverySnapshot,
)
from app.services.planning_research_service import (
    PlanningResearch,
    PlanningResearchService,
    ResearchEvidence,
)
from app.services.planning_weather import itinerary_weather_notes
from app.services.standalone_search_service import (
    FlightDealsRequest,
    HotelsRequest,
    SearchReply,
    StandaloneRequest,
    StandaloneSearchService,
)
from app.services.trip_requirements_policy import TripRequirementsPolicy

Output = TypeVar("Output", bound=BaseModel)
logger = logging.getLogger(__name__)


def _travel_options(
    research: PlanningResearch, kind: str
) -> list[tuple[str, tuple[ResearchEvidence, ...]]]:
    """Return stable user-facing choices; a round trip is one flight option."""
    if kind == "flight":

        def option_id(item: ResearchEvidence) -> str:
            return item.source_id or item.id.removesuffix("-return")

        outbound = [
            item
            for item in research.evidence
            if item.kind == "flight"
            and item.alternative_start_date is None
            and not item.id.endswith("-return")
        ]
        return [
            (
                option_id(item),
                tuple(
                    candidate
                    for candidate in research.evidence
                    if candidate.kind == "flight"
                    and candidate.alternative_start_date is None
                    and option_id(candidate) == option_id(item)
                ),
            )
            for item in outbound
        ]
    return [(item.id, (item,)) for item in research.evidence if item.kind == "hotel"]


def _flight_date_options(
    research: PlanningResearch,
) -> list[tuple[str, tuple[ResearchEvidence, ...]]]:
    alternatives = sorted(
        {
            (item.alternative_start_date, item.alternative_end_date)
            for item in research.evidence
            if item.kind == "flight"
            and item.alternative_start_date is not None
            and item.alternative_end_date is not None
        }
    )
    return [
        (
            f"{departure.isoformat()}|{returning.isoformat()}",
            tuple(
                item
                for item in research.evidence
                if item.kind == "flight"
                and item.alternative_start_date == departure
                and item.alternative_end_date == returning
            ),
        )
        for departure, returning in alternatives
    ]


def _selected_flight_date_pair(
    research: PlanningResearch,
    selection_id: str,
) -> tuple[date, date] | None:
    for option_id, evidence in _flight_date_options(research):
        if option_id != selection_id or not evidence:
            continue
        departure = evidence[0].alternative_start_date
        returning = evidence[0].alternative_end_date
        if departure is not None and returning is not None:
            return departure, returning
    return None


def _has_no_verified_options(research: PlanningResearch, kind: str) -> bool:
    return any(
        warning.startswith(f"{kind}s: no verified options")
        for warning in research.warnings
    )


def _selection_text(
    *,
    kind: str,
    options: list[tuple[str, tuple[ResearchEvidence, ...]]],
    research: PlanningResearch,
    language: str,
    no_results: bool = False,
    nearby_search_note: str | None = None,
) -> str:
    """Render bounded provider results and a clear next action in chat."""
    if no_results:
        if kind == "flight":
            text = (
                "Aap ki tareekh aur route ke liye koi verified flight option nahi mili. "
                "Itinerary abhi generate nahi ki. Tareekh ya route badlein, ya kahen ke flight khud arrange karenge."
                if language == "ur-Latn"
                else "No verified flights were found for those dates and route, so I have not generated the itinerary. Change the dates or route, or tell me you will arrange flights yourself."
            )
            if nearby_search_note == "checked":
                text += (
                    " Qareebi dates par aik din pehle/baad, trip ki muddat barqarar rakh kar, search ki; wahan bhi option nahi mili."
                    if language == "ur-Latn"
                    else " I also checked one day earlier and later while keeping the trip length the same, but found no verified options."
                )
            elif nearby_search_note == "incomplete":
                text += (
                    " Provider issue ki wajah se qareebi dates verify nahi ho sakin."
                    if language == "ur-Latn"
                    else " A provider issue prevented me from verifying nearby dates."
                )
            return text
        return (
            "Aap ke stay ke liye koi verified hotel option nahi mila. Itinerary abhi generate nahi ki. "
            "Tareekhein ya budget badlein, ya kahen ke accommodation khud arrange karenge."
            if language == "ur-Latn"
            else "No verified hotels were found for those stay dates, so I have not generated the itinerary. Change the dates or budget, or tell me you will arrange accommodation yourself."
        )

    label = "flight" if kind == "flight" else "hotel"
    if language == "ur-Latn":
        title = (
            "Flights mein se aik select karein:"
            if kind == "flight"
            else "Hotels mein se aik select karein:"
        )
        next_step = f"Reply mein {label} ka option number bhejein, ya dates/route/budget badal dein."
    else:
        title = (
            "Choose one flight before I build the itinerary:"
            if kind == "flight"
            else "Choose one hotel before I build the itinerary:"
        )
        next_step = f"Reply with the {label} option number, or change the dates, route, or budget."
    lines = [title]
    for index, (_, evidence) in enumerate(options, start=1):
        primary = evidence[0]
        if kind == "flight":
            outbound = primary.description
            returning = next(
                (item.description for item in evidence if item.id.endswith("-return")),
                None,
            )
            detail = f"{primary.name}; outbound {outbound}"
            if returning:
                detail += f"; return {returning}"
        else:
            card = primary.hotel_card
            detail = f"{primary.name}, {primary.location}"
            if card and card.price:
                detail += f"; {card.price.amount} {card.price.currency} total"
            if card and card.review_score is not None:
                detail += f"; review score {card.review_score}/10"
        lines.append(f"{index}. {detail[:500]}")
    lines.append(next_step)
    return "\n".join(lines)


def _flight_date_selection_text(
    options: list[tuple[str, tuple[ResearchEvidence, ...]]],
    *,
    language: str,
) -> str:
    if language == "ur-Latn":
        lines = [
            "Aap ki exact trip dates par flight verify nahi hui. Trip ki muddat barqarar rakhte hue qareebi dates par yeh options mile:",
        ]
    else:
        lines = [
            "No flight was verified for your exact trip dates. Keeping the trip length the same, these nearby date options have flights:",
        ]
    for index, (date_key, evidence) in enumerate(options, start=1):
        departure_text, return_text = date_key.split("|", maxsplit=1)
        grouped_offers: dict[str, list[ResearchEvidence]] = {}
        for item in evidence:
            offer_id = item.source_id or item.id.removesuffix("-return")
            grouped_offers.setdefault(offer_id, []).append(item)
        lines.append(
            f"Date option {index}: {departure_text} to {return_text} "
            f"({len(grouped_offers)} verified flight option(s))"
        )
        for offer_index, offer_evidence in enumerate(grouped_offers.values(), start=1):
            outbound = next(
                (item for item in offer_evidence if not item.id.endswith("-return")),
                offer_evidence[0],
            )
            returning = next(
                (item for item in offer_evidence if item.id.endswith("-return")),
                None,
            )
            detail = f"  {offer_index}. {outbound.name}; {outbound.description}"
            if returning:
                detail += f"; return {returning.description}"
            lines.append(detail[:700])
    lines.append(
        "Reply with a date option number to update the trip dates and run an exact flight search. Your current dates will stay unchanged unless you choose one."
        if language == "en"
        else "Tareekh chunne ke liye date option ka number bhejein. Phir trip dates update kar ke exact flight search hogi. Aap ke mojooda dates aap ke intikhab tak nahi badlein ge."
    )
    return "\n".join(lines)


def _match_selection(
    message: str,
    pending: PendingTravelSelection,
    research: PlanningResearch,
) -> str | None:
    """Resolve an explicit number or exact option name without model guessing."""
    if pending.reason != "choose":
        return None
    available = (
        _flight_date_options(research)
        if pending.kind == "flight_dates"
        else _travel_options(research, pending.kind)
    )
    options = [option for option in available if option[0] in pending.option_ids]
    normalized = message.strip().casefold()
    match = re.fullmatch(
        r"(?:(?:i\s+)?(?:choose|select|pick|want|take|go\s+with)\s+)?"
        r"(?:(?:the\s+)?(?:flight|hotel|date)\s+)?(?:option\s+)?(?:number\s+)?"
        r"(10|[1-9])(?:\s+please)?[.!]?",
        normalized,
    )
    if match:
        index = int(match.group(1)) - 1
        return options[index][0] if index < len(options) else None
    exact = [
        option[0]
        for option in options
        if any(item.name.casefold() == normalized for item in option[1])
    ]
    return exact[0] if len(exact) == 1 else None


def _research_for_selection(
    research: PlanningResearch,
    *,
    flight_id: str | None,
    hotel_id: str | None,
) -> PlanningResearch:
    """Keep only the user-selected flight and hotel in synthesis evidence."""
    evidence = tuple(
        item
        for item in research.evidence
        if (
            item.kind == "flight"
            and flight_id is not None
            and item.alternative_start_date is None
            and (item.source_id or item.id.removesuffix("-return")) == flight_id
        )
        or (item.kind == "hotel" and hotel_id is not None and item.id == hotel_id)
        or item.kind == "place"
    )
    return research.model_copy(update={"evidence": evidence})


def _selected_deal(message: str, snapshot: DealDiscoverySnapshot) -> DealChoice | None:
    """Resolve only an explicit displayed deal number or exact option ID."""
    normalized = message.strip().casefold()
    match = re.fullmatch(
        r"(?:(?:i\s+)?(?:choose|select|pick|want|take|go\s+with)\s+)?"
        r"(?:(?:the\s+)?deal\s+)?(?:option\s+)?(?:number\s+)?"
        r"(10|[1-9])(?:\s+please)?[.!]?",
        normalized,
    )
    if match:
        index = int(match.group(1)) - 1
        options = snapshot.result.options
        return options[index] if index < len(options) else None
    return next(
        (
            option
            for option in snapshot.result.options
            if option.option_id == normalized
        ),
        None,
    )


def _deal_evidence(choice: DealChoice) -> ResearchEvidence:
    deal = choice.deal
    return ResearchEvidence(
        id=f"deal-{choice.option_id}",
        kind="flight",
        name=deal.airline or "Flight deal",
        location=deal.destination,
        description=(
            f"Provider-reported round-trip deal {deal.origin} to {deal.destination}, "
            f"{deal.start_date.isoformat()} to {deal.end_date.isoformat()}, "
            f"{deal.price} {deal.currency}. Flight times and final fare are not verified."
        ),
        source_id=choice.option_id,
        source_urls=(deal.flight_link,),
    )


class StructuredOutputValidationError(ValueError):
    """A model response failed its expected structured-output schema."""

    def __init__(
        self,
        *,
        schema_name: str,
        schema: type[BaseModel],
        validation_error: ValidationError,
        allowed_fields: set[str],
    ) -> None:
        # Input stays in memory only long enough to derive its safe type/length.
        # Never attach raw Pydantic errors to logs or exception messages.
        errors = validation_error.errors(
            include_url=False, include_context=True, include_input=True
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
        self.error_details = tuple(
            _safe_validation_detail(item, _schema_field_names(schema))
            for item in errors[:12]
        )
        super().__init__(f"Model response does not match {schema_name}.")


def _schema_field_names(schema: type[BaseModel]) -> set[str]:
    """Collect model-declared names so arbitrary response keys stay redacted."""
    names: set[str] = set()
    pending: list[type[BaseModel]] = [schema]
    visited: set[type[BaseModel]] = set()
    while pending:
        model = pending.pop()
        if model in visited:
            continue
        visited.add(model)
        names.add(model.__name__)
        for field_name, field in model.model_fields.items():
            names.add(field_name)
            candidates = [field.annotation, *get_args(field.annotation)]
            for candidate in candidates:
                if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                    pending.append(candidate)
    return names


def _safe_validation_detail(
    error: dict[str, object], schema_fields: set[str]
) -> dict[str, object]:
    """Describe a schema failure without logging model or user-provided values."""
    location = error.get("loc", ())
    error_type = error.get("type")
    path: list[str | int] = []
    for part in location if isinstance(location, tuple) else ():
        if isinstance(part, int):
            path.append(part)
        elif isinstance(part, str):
            if part in schema_fields:
                path.append(part)
            else:
                path.append(
                    "<extra_field>" if error_type == "extra_forbidden" else "<variant>"
                )
    result: dict[str, object] = {
        "type": error_type if isinstance(error_type, str) else "unknown",
        "path": path[:8],
        "input_present": "input" in error,
    }
    value = error.get("input")
    result["input_kind"] = (
        "null"
        if value is None
        else (
            "boolean"
            if isinstance(value, bool)
            else (
                "number"
                if isinstance(value, (int, float))
                else (
                    "string"
                    if isinstance(value, str)
                    else (
                        "object"
                        if isinstance(value, dict)
                        else "array"
                        if isinstance(value, (list, tuple))
                        else "other"
                    )
                )
            )
        )
    )
    if isinstance(value, str):
        result["input_length"] = len(value)
    elif isinstance(value, (dict, list, tuple)):
        result["input_size"] = len(value)
    context = error.get("ctx")
    expected = context.get("expected") if isinstance(context, dict) else None
    if error_type == "literal_error" and isinstance(expected, str):
        result["expected"] = expected[:200]
    return result


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
            "prompt_version": (
                REQUIREMENTS_PROMPT_VERSION
                if stage == "requirements"
                else SYNTHESIS_PROMPT_VERSION
            ),
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
            schema=schema,
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


def validate_researched_itinerary(
    generated: ResearchedItinerary,
    *,
    requirements: TripRequirements,
    research: PlanningResearch,
    language: str = "en",
    required_evidence_ids: set[str] | None = None,
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
                starts_at=(
                    source.starts_at if source.kind == "flight" else item.starts_at
                ),
                ends_at=source.ends_at if source.kind == "flight" else item.ends_at,
                start_time_zone=(
                    source.start_time_zone
                    if source.kind == "flight"
                    else research.time_zone
                ),
                end_time_zone=(
                    source.end_time_zone
                    if source.kind == "flight"
                    else research.time_zone
                ),
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
                "breakfast": (
                    "Breakfast",
                    "Suggested breakfast break; choose a suitable venue.",
                ),
                "lunch": ("Lunch", "Suggested lunch break; choose a suitable venue."),
                "dinner": (
                    "Dinner",
                    "Suggested dinner break; choose a suitable venue.",
                ),
                "hike": (
                    "Optional hike",
                    "Allow time for your interests and pace; check trail access and weather.",
                ),
                "photography": (
                    "Photography time",
                    "Suggested time for photography; confirm access before visiting.",
                ),
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
                    "breakfast": (
                        "Nashta",
                        "Nashtay ka tajweez shuda waqfa; munasib jagah chunain.",
                    ),
                    "lunch": (
                        "Dopehar ka khana",
                        "Dopehar ke khanay ka waqfa; munasib jagah chunain.",
                    ),
                    "dinner": (
                        "Raat ka khana",
                        "Raat ke khanay ka waqfa; munasib jagah chunain.",
                    ),
                    "hike": (
                        "Ikhtiyari hiking",
                        "Apni dilchaspi aur raftaar ke mutabiq waqt rakhein; rasta aur mausam check karein.",
                    ),
                    "photography": (
                        "Tasveerain lenay ka waqt",
                        "Tasveerain lenay ke liye tajweez shuda waqt; rasai pehle check karein.",
                    ),
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
            elif starts is None and item.item_type != ItineraryItemType.NOTE:
                raise ValueError(
                    "Every activity requires start/end times in the verified destination timezone"
                )
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
    if required_evidence_ids:
        referenced_ids = {item.evidence_id for item in generated.items}
        if not required_evidence_ids.issubset(referenced_ids):
            raise ValueError(
                "Itinerary must include the selected flight and hotel options"
            )
    if research.time_zone is not None and {
        item["day_number"] for item in normalized if item["starts_at"] is not None
    } != set(range(1, days + 1)):
        raise ValueError("Each trip day requires at least one scheduled activity")
    weather_notes = (
        itinerary_weather_notes(
            start_date=start,
            end_date=end,
            forecast=research.weather_forecast,
            language=language,
        )
        if research.weather_requested or research.weather_forecast is not None
        else []
    )
    if len(normalized) + len(weather_notes) > MAX_ITINERARY_ITEMS:
        raise ValueError(
            "Too many schedule items; reserve one weather note per trip day"
        )
    normalized.extend(note.model_dump() for note in weather_notes)
    normalized.sort(key=lambda item: item["day_number"])
    return GeneratedItinerary(
        summary=(
            "Aap ki safar ki zarooriyat par mabni draft plan. Activity aur transfer ke auqat andazan hain."
            if language == "ur-Latn"
            else "Draft itinerary based on your trip requirements. Activity and transfer times are estimates."
        ),
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
        if (
            previous.pending_travel_selection
            and previous.pending_travel_selection.kind == "deal"
        ):
            if previous.deal_discovery is not None:
                snapshot = DealDiscoverySnapshot.model_validate(previous.deal_discovery)
                choice = _selected_deal(state["latest_message"], snapshot)
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
                        **route_planning_requirements(updated, preferences=preferences),
                        "requirements_changed": True,
                    }
        if previous.pending_travel_selection and previous.research:
            stored_research = PlanningResearch.model_validate(previous.research)
            selected_id = _match_selection(
                state["latest_message"],
                previous.pending_travel_selection,
                stored_research,
            )
            if selected_id is not None:
                if previous.pending_travel_selection.kind == "flight_dates":
                    selected_dates = _selected_flight_date_pair(
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
                        **route_planning_requirements(
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
                return route_planning_requirements(
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
            deal_discovery=base.deal_discovery,
            selected_deal=None if clear_flight else base.selected_deal,
            selected_flight_id=None if clear_flight else base.selected_flight_id,
            selected_hotel_id=None if clear_hotel else base.selected_hotel_id,
            nearby_flight_dates_checked=(
                False if clear_flight else base.nearby_flight_dates_checked
            ),
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
        planning = state["planning"]
        req = planning.requirements

        if deal_discovery_service is None:
            return {
                "assistant_response": "Flight deal search is currently unavailable.",
            }

        assert req.origin is not None
        assert req.destination is not None
        assert req.date_window_start is not None
        assert req.date_window_end is not None
        assert req.adults is not None

        minor_ages = req.minor_ages or ()
        infant_ages = [age for age in minor_ages if age < 2]
        infants_on_lap = sum(req.infant_on_lap or ())
        infants_in_seat = len(infant_ages) - infants_on_lap
        children = len(minor_ages) - len(infant_ages)

        request = DealDiscoveryInput(
            origin=req.origin,
            destination=req.destination,
            window_start=req.date_window_start,
            window_end=req.date_window_end,
            adults=req.adults,
            children=children,
            infants_in_seat=infants_in_seat,
            infants_on_lap=infants_on_lap,
            currency=req.budget_currency or "USD",
        )
        request_key = hashlib.sha256(
            request.model_dump_json().encode("utf-8")
        ).hexdigest()
        cached = (
            DealDiscoverySnapshot.model_validate(planning.deal_discovery)
            if planning.deal_discovery is not None
            else None
        )
        now = utc_now()
        if (
            cached is not None
            and cached.request_key == request_key
            and timedelta(0) <= now - cached.searched_at < timedelta(minutes=10)
        ):
            snapshot = cached
        else:
            result = await deal_discovery_service.discover(request=request)
            snapshot = DealDiscoverySnapshot(
                request_key=request_key, searched_at=now, request=request, result=result
            )
        result = snapshot.result

        if result.status == "airport_resolution_required":
            message = (
                "Please specify the departure and destination airports "
                "so I can search deals."
            )
        elif result.status == "no_deals":
            message = (
                "No matching deals appeared for this route and date window. "
                "This does not mean there are no flights. Please choose exact "
                "travel dates if you want me to check flight options."
            )
        else:
            lines = ["Matching flight deals:"]
            for index, option in enumerate(result.options, start=1):
                deal = option.deal
                stops = str(deal.stops) if deal.stops is not None else "unknown"
                lines.append(
                    f"{index}. {deal.start_date:%d %b %Y}–"
                    f"{deal.end_date:%d %b %Y} "
                    f"({deal.duration_days} days): "
                    f"{deal.price} {deal.currency}; "
                    f"{deal.airline or 'airline unspecified'}, "
                    f"{stops} stops. {deal.flight_link}"
                )
            lines.append(
                "These are provider-reported deals, not guaranteed bookings. "
                "Reply with a deal option number or give your preferred exact dates."
            )
            message = "\n".join(lines)

        if (
            result.status != "airport_resolution_required"
            and req.duration_days is not None
        ):
            last_start = req.date_window_end - timedelta(days=req.duration_days - 1)
            starts = {
                req.date_window_start,
                req.date_window_start + (last_start - req.date_window_start) // 2,
                last_start,
            }
            suggestions = sorted(starts)[:3]
            message += (
                "\n\nUnverified date examples for your requested trip length: "
                + "; ".join(
                    f"{start.isoformat()} to {(start + timedelta(days=req.duration_days - 1)).isoformat()}"
                    for start in suggestions
                )
            )
            message += ". Flight availability has not been checked for these dates."

        pending = (
            PendingTravelSelection(
                kind="deal",
                option_ids=tuple(option.option_id for option in result.options),
                reason="choose",
            )
            if result.options
            else None
        )
        updated = PlanningState.model_validate(
            {
                **planning.model_dump(),
                "deal_discovery": snapshot.model_dump(mode="json"),
                "pending_travel_selection": pending,
            }
        )
        return {"assistant_response": message, "planning": updated}

    async def research(state: PlanningGraphState) -> dict[str, object]:
        planning = state["planning"]
        selected_deal = (
            DealChoice.model_validate(planning.selected_deal)
            if planning.selected_deal is not None
            else None
        )
        selected_evidence = (
            _deal_evidence(selected_deal) if selected_deal is not None else None
        )
        key = research_service.cache_key(planning.requirements)
        cached = (
            PlanningResearch.model_validate(planning.research)
            if planning.research
            else None
        )
        now = utc_now()
        cached_has_selected_deal = selected_evidence is None or (
            cached is not None
            and any(item.id == selected_evidence.id for item in cached.evidence)
        )
        reusable = (
            cached is not None
            and cached_has_selected_deal
            and planning.research_key == key
            and timedelta(0) <= now - cached.searched_at < timedelta(minutes=5)
            and not cached.warnings
            and not cached.guidance
            and all(
                item.expires_at is None or item.expires_at > now
                for item in cached.evidence
            )
        )
        reusable_for_selection = (
            cached is not None
            and cached_has_selected_deal
            and (
                planning.pending_travel_selection is not None
                or planning.selected_flight_id is not None
                or planning.selected_hotel_id is not None
            )
            and planning.research_key == key
            and timedelta(0) <= now - cached.searched_at < timedelta(minutes=5)
            and all(
                item.expires_at is None or item.expires_at > now
                for item in cached.evidence
            )
        )
        if reusable or reusable_for_selection:
            result = cached
        elif selected_evidence is not None:
            result = await research_service.research(
                planning.requirements, preselected_flight=selected_evidence
            )
        elif planning.nearby_flight_dates_checked:
            result = await research_service.research(
                planning.requirements,
                allow_nearby_flight_dates=False,
            )
        else:
            result = await research_service.research(planning.requirements)
        assert result is not None
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
        selected_flight_id = planning.selected_flight_id
        selected_hotel_id = planning.selected_hotel_id
        if planning.requirements.transport == "flight":
            flight_options = _travel_options(result, "flight")
            nearby_dates_checked = bool(_flight_date_options(result)) or any(
                "nearby" in warning or "one-day-shifted" in warning
                for warning in result.warnings
                if warning.startswith("flights:")
            )
            available_flight_ids = {option_id for option_id, _ in flight_options}
            if selected_flight_id not in available_flight_ids:
                selected_flight_id = None
            if selected_flight_id is None:
                date_options = _flight_date_options(result)
                if not flight_options and date_options:
                    pending = PendingTravelSelection(
                        kind="flight_dates",
                        option_ids=tuple(option_id for option_id, _ in date_options),
                        reason="choose",
                    )
                    planning = planning.model_copy(
                        update={
                            "research": result.model_dump(mode="json"),
                            "research_key": key,
                            "pending_travel_selection": pending,
                            "selected_flight_id": None,
                            "nearby_flight_dates_checked": True,
                        }
                    )
                    return {
                        "research": result,
                        "route": "respond",
                        "planning": planning,
                        "assistant_response": _flight_date_selection_text(
                            date_options,
                            language=planning.language,
                        ),
                    }
                if len(flight_options) > 1:
                    pending = PendingTravelSelection(
                        kind="flight",
                        option_ids=tuple(option_id for option_id, _ in flight_options),
                        reason="choose",
                    )
                elif len(flight_options) == 1:
                    selected_flight_id = flight_options[0][0]
                    pending = None
                elif _has_no_verified_options(result, "flight"):
                    pending = PendingTravelSelection(kind="flight", reason="no_results")
                else:
                    pending = None
                if pending is not None:
                    planning = planning.model_copy(
                        update={
                            "research": result.model_dump(mode="json"),
                            "research_key": key,
                            "pending_travel_selection": pending,
                            "selected_flight_id": selected_flight_id,
                            "nearby_flight_dates_checked": nearby_dates_checked,
                        }
                    )
                    return {
                        "research": result,
                        "route": "respond",
                        "planning": planning,
                        "assistant_response": _selection_text(
                            kind="flight",
                            options=flight_options,
                            research=result,
                            language=planning.language,
                            no_results=pending.reason == "no_results",
                            nearby_search_note=(
                                "incomplete"
                                if any(
                                    "nearby-date checks failed" in warning
                                    for warning in result.warnings
                                )
                                else (
                                    "checked"
                                    if any(
                                        "one-day-shifted trip dates" in warning
                                        or "already checked" in warning
                                        for warning in result.warnings
                                    )
                                    else None
                                )
                            ),
                        ),
                    }

        if planning.requirements.needs_lodging:
            hotel_options = _travel_options(result, "hotel")
            available_hotel_ids = {option_id for option_id, _ in hotel_options}
            if selected_hotel_id not in available_hotel_ids:
                selected_hotel_id = None
            if selected_hotel_id is None:
                if len(hotel_options) > 1:
                    pending = PendingTravelSelection(
                        kind="hotel",
                        option_ids=tuple(option_id for option_id, _ in hotel_options),
                        reason="choose",
                    )
                elif len(hotel_options) == 1:
                    selected_hotel_id = hotel_options[0][0]
                    pending = None
                elif _has_no_verified_options(result, "hotel"):
                    pending = PendingTravelSelection(kind="hotel", reason="no_results")
                else:
                    pending = None
                if pending is not None:
                    planning = planning.model_copy(
                        update={
                            "research": result.model_dump(mode="json"),
                            "research_key": key,
                            "pending_travel_selection": pending,
                            "selected_flight_id": selected_flight_id,
                            "selected_hotel_id": selected_hotel_id,
                        }
                    )
                    return {
                        "research": result,
                        "route": "respond",
                        "planning": planning,
                        "assistant_response": _selection_text(
                            kind="hotel",
                            options=hotel_options,
                            research=result,
                            language=planning.language,
                            no_results=pending.reason == "no_results",
                        ),
                    }
        result = _research_for_selection(
            result,
            flight_id=selected_flight_id,
            hotel_id=selected_hotel_id,
        )
        return {
            "research": result,
            "route": "synthesize",
            "planning": planning.model_copy(
                update={
                    "research_key": key,
                    "research": result.model_dump(mode="json"),
                    "pending_travel_selection": None,
                    "selected_flight_id": selected_flight_id,
                    "selected_hotel_id": selected_hotel_id,
                }
            ),
        }

    async def standalone(state: PlanningGraphState) -> dict[str, object]:
        if standalone_service is None:
            return {"assistant_response": "Live standalone search is unavailable."}
        request = state["standalone_request"]
        planning = state["planning"]
        if isinstance(request, FlightDealsRequest):
            canonical = DealDiscoveryInput.model_validate(
                request.arguments.model_dump()
            )
            request_key = hashlib.sha256(
                canonical.model_dump_json().encode("utf-8")
            ).hexdigest()
            now = utc_now()
            cached = (
                DealDiscoverySnapshot.model_validate(planning.deal_discovery)
                if planning.deal_discovery is not None
                else None
            )
            reusable = (
                cached is not None
                and cached.request_key == request_key
                and cached.reply is not None
                and cached.result.status != "airport_resolution_required"
                and timedelta(0) <= now - cached.searched_at < timedelta(minutes=10)
            )
            if reusable:
                assert cached is not None
                snapshot = cached
                reply = SearchReply(content=cached.reply, deal_result=cached.result)
            else:
                reply = await standalone_service.search(request)
                snapshot = (
                    DealDiscoverySnapshot(
                        request_key=request_key,
                        searched_at=now,
                        request=canonical,
                        result=reply.deal_result,
                        reply=reply.content,
                    )
                    if reply.deal_result is not None
                    and reply.deal_result.status != "airport_resolution_required"
                    else None
                )
            result = snapshot.result if snapshot is not None else reply.deal_result
            pending = (
                PendingTravelSelection(
                    kind="deal",
                    option_ids=tuple(option.option_id for option in result.options),
                    reason="choose",
                )
                if result is not None and result.options
                else None
            )
            updated = PlanningState.model_validate(
                {
                    **planning.model_dump(),
                    "deal_discovery": (
                        snapshot.model_dump(mode="json")
                        if snapshot is not None
                        else None
                    ),
                    "pending_travel_selection": pending,
                    "pending_search": (
                        request.model_dump(mode="json")
                        if reply.input_required
                        else None
                    ),
                }
            )
            return {
                "assistant_response": reply.content,
                "clarification": reply.clarification,
                "planning": updated,
            }

        reply = await standalone_service.search(request)
        planning = state["planning"].model_copy(
            update={
                "pending_search": (
                    request.model_dump(mode="json") if reply.input_required else None
                ),
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
            "research": research.model_dump(mode="json", exclude={"weather_forecast"}),
            "previous_itinerary": planning.itinerary,
            "request": state["latest_message"],
            "profile_preferences": state.get(
                "planning_preferences",
                PlanningPreferences(),
            ).model_dump(mode="json", exclude={"home_city", "home_country_code"}),
        }
        weather_notes = (
            itinerary_weather_notes(
                start_date=planning.requirements.start_date,
                end_date=planning.requirements.resolved_end_date,
                forecast=research.weather_forecast,
                language=planning.language,
            )
            if research.weather_requested or research.weather_forecast is not None
            else []
        )
        data["weather_outlook"] = [
            {"day_number": note.day_number, "outlook": note.description}
            for note in weather_notes
        ]
        data["max_schedule_items"] = MAX_ITINERARY_ITEMS - len(weather_notes)
        required_evidence_ids = {
            item.id
            for item in research.evidence
            if (
                item.kind == "flight"
                and planning.selected_flight_id is not None
                and (item.source_id or item.id.removesuffix("-return"))
                == planning.selected_flight_id
            )
            or (item.kind == "hotel" and item.id == planning.selected_hotel_id)
        }
        data["required_selected_evidence_ids"] = sorted(required_evidence_ids)
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
                    required_evidence_ids=required_evidence_ids,
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
        if weather_notes:
            text += "\n\n" + "\n\n".join(
                f"{note.title}\n{note.description}" for note in weather_notes
            )
        if research.time_zone is None:
            text += (
                "\n\nDestination ka timezone verify nahi hua; activity timings dastiyab nahi."
                if planning.language == "ur-Latn"
                else "\n\nDestination timezone could not be verified; activity timings are unavailable."
            )
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
