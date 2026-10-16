"""Flexible-date flight deal discovery workflow."""

import hashlib
import re
from datetime import timedelta
from typing import NotRequired, TypedDict

from app.common.time import utc_now
from app.domain.clarifications import (
    TravelClarification,
    build_airport_input_request,
)
from app.domain.planning import PendingTravelSelection, PlanningState
from app.services.deal_discovery_service import (
    DealChoice,
    DealDiscoveryInput,
    DealDiscoveryService,
    DealDiscoverySnapshot,
)
from app.services.planning_research_service import ResearchEvidence


class DealWorkflowResult(TypedDict):
    """State updates returned by the deal-discovery graph node."""

    assistant_response: str
    planning: NotRequired[PlanningState]
    clarification: NotRequired[TravelClarification | None]


class DealDiscoveryWorkflow:
    """Discover and cache flexible-date flight deals."""

    def __init__(
        self,
        *,
        deal_discovery_service: DealDiscoveryService | None,
    ) -> None:
        self._service = deal_discovery_service

    async def run(
        self,
        *,
        planning: PlanningState,
    ) -> DealWorkflowResult:
        """Run deal discovery using validated planning requirements."""

        if self._service is None:
            return {
                "assistant_response": ("Flight deal search is currently unavailable."),
            }

        request = self._build_request(planning)
        snapshot = await self._get_snapshot(
            planning=planning,
            request=request,
        )
        result = snapshot.result

        message, clarification = self._render_result(
            planning=planning,
            snapshot=snapshot,
        )

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

        return {
            "assistant_response": message,
            "clarification": clarification,
            "planning": updated,
        }

    @staticmethod
    def _build_request(
        planning: PlanningState,
    ) -> DealDiscoveryInput:
        requirements = planning.requirements

        if requirements.origin is None:
            raise ValueError("Deal discovery requires an origin")
        if requirements.destination is None:
            raise ValueError("Deal discovery requires a destination")
        if requirements.date_window_start is None:
            raise ValueError("Deal discovery requires a date-window start")
        if requirements.date_window_end is None:
            raise ValueError("Deal discovery requires a date-window end")
        if requirements.adults is None:
            raise ValueError("Deal discovery requires an adult count")

        minor_ages = requirements.minor_ages or ()
        infant_ages = tuple(age for age in minor_ages if age < 2)
        infants_on_lap = sum(requirements.infant_on_lap or ())
        infants_in_seat = len(infant_ages) - infants_on_lap
        children = len(minor_ages) - len(infant_ages)

        return DealDiscoveryInput(
            origin=requirements.origin,
            destination=requirements.destination,
            window_start=requirements.date_window_start,
            window_end=requirements.date_window_end,
            adults=requirements.adults,
            children=children,
            infants_in_seat=infants_in_seat,
            infants_on_lap=infants_on_lap,
            currency=requirements.budget_currency or "USD",
        )

    async def _get_snapshot(
        self,
        *,
        planning: PlanningState,
        request: DealDiscoveryInput,
    ) -> DealDiscoverySnapshot:
        request_key = hashlib.sha256(
            request.model_dump_json().encode("utf-8")
        ).hexdigest()

        cached = (
            DealDiscoverySnapshot.model_validate(planning.deal_discovery)
            if planning.deal_discovery is not None
            else None
        )
        now = utc_now()

        reusable = (
            cached is not None
            and cached.request_key == request_key
            and cached.result.status != "airport_resolution_required"
            and timedelta(0) <= now - cached.searched_at < timedelta(minutes=10)
        )

        if reusable:
            return cached

        if self._service is None:
            # run() already prevents this path. This guard keeps
            # the private method safe if called independently.
            raise RuntimeError("Deal discovery service is unavailable")

        result = await self._service.discover(
            request=request,
        )

        return DealDiscoverySnapshot(
            request_key=request_key,
            searched_at=now,
            request=request,
            result=result,
        )

    @classmethod
    def _render_result(
        cls,
        *,
        planning: PlanningState,
        snapshot: DealDiscoverySnapshot,
    ) -> tuple[str, TravelClarification | None]:
        result = snapshot.result

        if result.status == "airport_resolution_required":
            message, clarification = cls._render_airport_clarification(snapshot)
        elif result.status == "no_deals":
            message = (
                "No matching deals appeared for this route and "
                "date window. This does not mean there are no "
                "flights. Please choose exact travel dates if "
                "you want me to check flight options."
            )
            clarification = None
        else:
            message = cls._render_deal_options(snapshot)
            clarification = None

        message += cls._render_date_examples(
            planning=planning,
            airport_resolution_required=(
                result.status == "airport_resolution_required"
            ),
        )

        return message, clarification

    @staticmethod
    def _render_airport_clarification(
        snapshot: DealDiscoverySnapshot,
    ) -> tuple[str, TravelClarification | None]:
        result = snapshot.result

        requests = [
            request
            for field, resolution in (
                ("origin", result.origin),
                ("destination", result.destination),
            )
            if (
                request := build_airport_input_request(
                    field=field,
                    resolution=resolution,
                )
            )
            is not None
        ]

        clarification = TravelClarification(requests=requests) if requests else None

        lines = [
            line
            for request in requests
            for line in (
                request.question,
                *(
                    f"{index}. {option.display_name}"
                    for index, option in enumerate(
                        request.options,
                        start=1,
                    )
                ),
            )
        ]

        message = "\n".join(lines) or (
            "I could not identify an airport for this route. "
            "Please clarify the city and country."
        )

        return message, clarification

    @staticmethod
    def _render_deal_options(
        snapshot: DealDiscoverySnapshot,
    ) -> str:
        lines = ["Matching flight deals:"]

        for index, option in enumerate(
            snapshot.result.options,
            start=1,
        ):
            deal = option.deal
            stops = str(deal.stops) if deal.stops is not None else "unknown"

            lines.append(
                f"{index}. "
                f"{deal.start_date:%d %b %Y}–"
                f"{deal.end_date:%d %b %Y} "
                f"({deal.duration_days} days): "
                f"{deal.price} {deal.currency}; "
                f"{deal.airline or 'airline unspecified'}, "
                f"{stops} stops. "
                f"{deal.flight_link}"
            )

        lines.append(
            "These are provider-reported deals, not guaranteed "
            "bookings. Reply with a deal option number or give "
            "your preferred exact dates."
        )

        return "\n".join(lines)

    @staticmethod
    def _render_date_examples(
        *,
        planning: PlanningState,
        airport_resolution_required: bool,
    ) -> str:
        requirements = planning.requirements
        duration = requirements.duration_days
        window_start = requirements.date_window_start
        window_end = requirements.date_window_end

        if (
            airport_resolution_required
            or duration is None
            or window_start is None
            or window_end is None
        ):
            return ""

        last_start = window_end - timedelta(days=duration - 1)
        starts = {
            window_start,
            window_start + (last_start - window_start) // 2,
            last_start,
        }
        suggestions = sorted(starts)[:3]

        examples = "; ".join(
            (
                f"{start.isoformat()} to "
                f"{(start + timedelta(days=duration - 1)).isoformat()}"
            )
            for start in suggestions
        )

        return (
            "\n\nUnverified date examples for your requested "
            f"trip length: {examples}. Flight availability has "
            "not been checked for these dates."
        )


def match_deal_selection(
    message: str,
    snapshot: DealDiscoverySnapshot,
) -> DealChoice | None:
    """Resolve an explicit displayed deal number or exact option ID."""

    normalized = message.strip().casefold()
    match = re.fullmatch(
        r"(?:(?:i\s+)?"
        r"(?:choose|select|pick|want|take|go\s+with)\s+)?"
        r"(?:(?:the\s+)?deal\s+)?"
        r"(?:option\s+)?(?:number\s+)?"
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
            if option.option_id.casefold() == normalized
        ),
        None,
    )


def deal_evidence(
    choice: DealChoice,
) -> ResearchEvidence:
    """Convert a selected deal into trusted synthesis evidence."""

    deal = choice.deal

    return ResearchEvidence(
        id=f"deal-{choice.option_id}",
        kind="flight",
        name=deal.airline or "Flight deal",
        location=deal.destination,
        description=(
            "Provider-reported round-trip deal "
            f"{deal.origin} to {deal.destination}, "
            f"{deal.start_date.isoformat()} to "
            f"{deal.end_date.isoformat()}, "
            f"{deal.price} {deal.currency}. "
            "Flight times and final fare are not verified."
        ),
        source_id=choice.option_id,
        source_urls=(deal.flight_link,),
    )
