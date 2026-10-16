"""Deterministic flight and hotel selection helpers."""

import re
from datetime import date

from app.domain.planning import PendingTravelSelection
from app.services.planning_research_service import PlanningResearch, ResearchEvidence

TravelOption = tuple[str, tuple[ResearchEvidence, ...]]


def travel_options(
    research: PlanningResearch,
    kind: str,
) -> list[TravelOption]:
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
                option_id(item=item),
                tuple(
                    candidate
                    for candidate in research.evidence
                    if candidate.kind == "flight"
                    and candidate.alternative_start_date is None
                    and option_id(item=candidate) == option_id(item=item)
                ),
            )
            for item in outbound
        ]

    return [(item.id, (item,)) for item in research.evidence if item.kind == "hotel"]


def flight_date_options(research: PlanningResearch) -> list[TravelOption]:
    """Group nearby-date flight evidence by departure and return dates."""
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


def selected_flight_date_pair(
    research: PlanningResearch,
    selection_id: str,
) -> tuple[date, date] | None:
    """Return the selected nearby departure and return date pair."""
    for option_id, evidence in flight_date_options(research=research):
        if option_id != selection_id or not evidence:
            continue
        departure = evidence[0].alternative_start_date
        returning = evidence[0].alternative_end_date
        if departure is not None and returning is not None:
            return departure, returning

    return None


def has_no_verified_options(research: PlanningResearch, kind: str) -> bool:
    """Return whether research explicitly verified that no options exist."""

    return any(
        warning.startswith(f"{kind}s: no verified options")
        for warning in research.warnings
    )


def selection_text(
    *,
    kind: str,
    options: list[TravelOption],
    language: str,
    no_results: bool = False,
    nearby_search_note: str | None = None,
) -> str:
    """Render bounded provider results and a clear next action in chat."""
    if no_results:
        if kind == "flight":
            text = (
                "Aap ki tareekh aur route ke liye koi verified flight option nahi "
                "mili. Itinerary abhi generate nahi ki. Tareekh ya route badlein, "
                "ya kahen ke flight khud arrange karenge."
                if language == "ur-Latn"
                else "No verified flights were found for those dates and route, so I "
                "have not generated the itinerary. Change the dates or route, or "
                "tell me you will arrange flights yourself."
            )
            if nearby_search_note == "checked":
                text += (
                    " Qareebi dates par aik din pehle/baad, trip ki muddat barqarar "
                    "rakh kar, search ki; wahan bhi option nahi mili."
                    if language == "ur-Latn"
                    else " I also checked one day earlier and later while keeping the "
                    "trip length the same, but found no verified options."
                )
            elif nearby_search_note == "incomplete":
                text += (
                    " Provider issue ki wajah se qareebi dates verify nahi ho sakin."
                    if language == "ur-Latn"
                    else " A provider issue prevented me from verifying nearby dates."
                )
            return text
        return (
            "Aap ke stay ke liye koi verified hotel option nahi mila. Itinerary "
            "abhi generate nahi ki. Tareekhein ya budget badlein, ya kahen ke "
            "accommodation khud arrange karenge."
            if language == "ur-Latn"
            else "No verified hotels were found for those stay dates, so I have not "
            "generated the itinerary. Change the dates or budget, or tell me you "
            "will arrange accommodation yourself."
        )
    label = "flight" if kind == "flight" else "hotel"
    if language == "ur-Latn":
        title = (
            "Flights mein se aik select karein:"
            if kind == "flight"
            else "Hotels mein se aik select karein:"
        )
        next_step = (
            f"Reply mein {label} ka option number bhejein, "
            "ya dates/route/budget badal dein."
        )
    else:
        title = (
            "Choose one flight before I build the itinerary:"
            if kind == "flight"
            else "Choose one hotel before I build the itinerary:"
        )
        next_step = (
            f"Reply with the {label} option number, "
            "or change the dates, route, or budget."
        )
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


def flight_date_selection_text(
    options: list[TravelOption],
    *,
    language: str,
) -> str:
    """Render nearby flight-date choices."""
    if language == "ur-Latn":
        lines = [
            "Aap ki exact trip dates par flight verify nahi hui. Trip ki muddat "
            "barqarar rakhte hue qareebi dates par yeh options mile:",
        ]
    else:
        lines = [
            "No flight was verified for your exact trip dates. Keeping the trip "
            "length the same, these nearby date options have flights:",
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
        (
            "Tareekh chunne ke liye date option ka number bhejein. Phir trip "
            "dates update kar ke exact flight search hogi. Aap ke mojooda dates "
            "aap ke intikhab tak nahi badlein ge."
        )
        if language == "ur-Latn"
        else (
            "Reply with a date option number to update the trip dates and run an "
            "exact flight search. Your current dates will stay unchanged unless "
            "you choose one."
        )
    )
    return "\n".join(lines)


def match_selection(
    message: str,
    pending: PendingTravelSelection,
    research: PlanningResearch,
) -> str | None:
    """Resolve an explicit number or exact option name without model guessing."""

    if pending.reason != "choose":
        return None
    available = (
        flight_date_options(research=research)
        if pending.kind == "flight_dates"
        else travel_options(research, pending.kind)
    )
    options = [option for option in available if option[0] in pending.option_ids]
    normalized = message.strip().casefold()
    match = re.fullmatch(
        r"(?:(?:i\s+)?(?:choose|select|pick|want|take|go\s+with)\s+)?"
        r"(?:(?:the\s+)?(?:flight|hotel|date)\s+)?"
        r"(?:option\s+)?(?:number\s+)?"
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


def research_for_selection(
    research: PlanningResearch, *, flight_id: str | None, hotel_id: str | None
) -> PlanningResearch:
    """Keep only the selected flight and hotel in synthesis evidence."""
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
