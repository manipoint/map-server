"""Itinerary synthesis workflow using researched travel evidence."""

from typing import TypedDict

from app.domain.itineraries import MAX_ITINERARY_ITEMS, ItineraryActivity
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.graph.model_response import ModelResponseRefusedError
from app.graph.planning.itinerary_validation import validate_researched_itinerary
from app.graph.planning.structured_output import (
    StructuredOutputValidationError,
    structured_call,
)
from app.graph.planning_prompts import SYNTHESIS_PROMPT
from app.graph.planning_schemas import ResearchedItinerary
from app.graph.schemas.itineraries import GeneratedItinerary
from app.graph.subgraphs.model_gateway import ModelGateway
from app.services.planning_research_service import PlanningResearch
from app.services.planning_weather import itinerary_weather_notes


class TripSynthesisResult(TypedDict):
    generated_itinerary: GeneratedItinerary
    assistant_response: str
    planning: PlanningState


class TripSynthesisWorkflow:
    """Create and validate an itinerary from trusted research evidence."""

    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
    ) -> None:
        self._model_gateway = model_gateway

    async def run(
        self,
        *,
        planning: PlanningState,
        research: PlanningResearch,
        latest_message: str,
        preferences: PlanningPreferences,
    ) -> TripSynthesisResult:
        weather_notes = self._weather_notes(planning=planning, research=research)
        required_evidence_ids = self._required_evidence_ids(
            planning=planning, research=research
        )
        data = self._build_input(
            planning=planning,
            research=research,
            latest_message=latest_message,
            preferences=preferences,
            weather_notes=weather_notes,
            required_evidence_ids=required_evidence_ids,
        )
        generated = await self._generate_with_repair(
            data=data,
            planning=planning,
            research=research,
            required_evidence_ids=required_evidence_ids,
        )
        update = planning.model_copy(
            update={
                "phase": "generated",
                "itinerary": generated.model_dump(mode="json"),
                "research": research.model_dump(mode="json"),
            }
        )
        return {
            "generated_itinerary": generated,
            "assistant_response": self._render_response(
                generated=generated,
                planning=planning,
                research=research,
                weather_notes=weather_notes,
            ),
            "planning": update,
        }

    def _build_input(
        self,
        *,
        planning: PlanningState,
        research: PlanningResearch,
        latest_message: str,
        preferences: PlanningPreferences,
        weather_notes: list[ItineraryActivity],
        required_evidence_ids: set[str],
    ) -> dict[str, object]:
        return {
            "requirements": planning.requirements.model_dump(mode="json"),
            "language": planning.language,
            "research": research.model_dump(mode="json", exclude={"weather_forecast"}),
            "previous_itinerary": planning.itinerary,
            "request": latest_message,
            "profile_preferences": preferences.model_dump(
                mode="json",
                exclude={"home_city", "home_country_code"},
            ),
            "weather_outlook": [
                {"day_number": note.day_number, "outlook": note.description}
                for note in weather_notes
            ],
            "max_schedule_items": MAX_ITINERARY_ITEMS - len(weather_notes),
            "required_selected_evidence_ids": sorted(required_evidence_ids),
        }

    async def _generate_with_repair(
        self,
        *,
        data: dict[str, object],
        planning: PlanningState,
        research: PlanningResearch,
        required_evidence_ids: set[str],
    ) -> GeneratedItinerary:
        model_data = dict(data)

        for attempt in range(2):
            try:
                proposed = await structured_call(
                    self._model_gateway,
                    ResearchedItinerary,
                    prompt=SYNTHESIS_PROMPT,
                    data=model_data,
                )
                return validate_researched_itinerary(
                    proposed,
                    requirements=planning.requirements,
                    research=research,
                    language=planning.language,
                    required_evidence_ids=required_evidence_ids,
                )
            except ModelResponseRefusedError:
                raise
            except ValueError as error:
                if attempt == 1:
                    raise
                model_data["repair"] = (
                    "Previous output failed validation. Rebuild using the exact "
                    "schema, all days, valid evidence IDs and non-overlapping times."
                )
                model_data["validation_failure"] = (
                    {
                        "fields": error.error_fields,
                        "errors": error.error_types,
                    }
                    if isinstance(error, StructuredOutputValidationError)
                    else str(error)[:200]
                )

        raise RuntimeError("Unreachable synthesis state")

    @staticmethod
    def _required_evidence_ids(
        *, planning: PlanningState, research: PlanningResearch
    ) -> set[str]:
        return {
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

    @staticmethod
    def _weather_notes(
        *, planning: PlanningState, research: PlanningResearch
    ) -> list[ItineraryActivity]:
        if not (research.weather_requested or research.weather_forecast is not None):
            return []
        start_date = planning.requirements.start_date
        end_date = planning.requirements.resolved_end_date

        if start_date is None or end_date is None:
            raise ValueError("Itinerary synthesis requires complete trip dates")
        return itinerary_weather_notes(
            start_date=start_date,
            end_date=end_date,
            forecast=research.weather_forecast,
            language=planning.language,
        )

    @staticmethod
    def _render_response(
        *,
        generated: GeneratedItinerary,
        planning: PlanningState,
        research: PlanningResearch,
        weather_notes: list[ItineraryActivity],
    ) -> str:
        text = generated.summary + (
            "\n\nYe draft plan hai. Availability aur total cost confirm karna baqi hai."
            if planning.language == "ur-Latn"
            else "\n\nThis is a draft. Availability and total cost still need "
            "confirmation."
        )

        if research.warnings:
            text += "\n\n" + "\n".join(research.warnings)

        if weather_notes:
            text += "\n\n" + "\n\n".join(
                f"{note.title}\n{note.description}" for note in weather_notes
            )
        if research.time_zone is None:
            text += (
                "\n\nDestination ka timezone verify nahi hua; activity timings "
                "dastiyab nahi."
                if planning.language == "ur-Latn"
                else "\n\nDestination timezone could not be verified; activity "
                "timings are unavailable."
            )
        return text
