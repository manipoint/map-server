"""Extract deterministic clarification data from graph tool results."""

from collections.abc import Iterator
from json import JSONDecodeError, loads

from langchain_core.messages import BaseMessage, ToolMessage
from pydantic import ValidationError

from app.domain.clarifications import AirportInputRequest, TravelClarification
from app.providers.airports.schemas import AirportResolution
from app.services.flight_search_preparation_service import (
    FlightSearchPreparationGuidance,
)

FLIGHT_SEARCH_TOOL_NAME = "search_flights"


def iter_tool_payloads(message: ToolMessage) -> Iterator[object]:
    """Yield structured candidates from supported LangChain tool formats."""

    artifact = message.artifact
    if isinstance(artifact, dict):
        structured_content = artifact.get("structured_content")
        if isinstance(structured_content, dict):
            yield structured_content.get("result", structured_content)

    content = message.content
    text_values: list[str] = []
    if isinstance(content, str):
        text_values.append(content)
    elif isinstance(content, list):
        text_values.extend(
            text
            for block in content
            if isinstance(block, dict) and isinstance((text := block.get("text")), str)
        )

    for text in text_values:
        try:
            yield loads(text)
        except (JSONDecodeError, TypeError):
            continue


def build_airport_input_request(
    *,
    field: str,
    resolution: AirportResolution,
) -> AirportInputRequest | None:
    """Map one unresolved airport result to a stable client request."""

    if resolution.status == "resolved":
        return None
    public_field = "origin_airport" if field == "origin" else "destination_airport"
    if resolution.status == "selection_required":
        question = f"Select an airport for {resolution.query}."
    else:
        question = (
            f"No airport matched {resolution.query}. "
            "Provide the city with country or region."
        )
    return AirportInputRequest(
        field=public_field,
        query=resolution.query,
        status=resolution.status,
        question=question,
        options=resolution.options,
    )


def extract_travel_clarification(
    messages: list[BaseMessage],
) -> TravelClarification | None:
    """Return the latest valid flight clarification, if the graph produced one."""

    for message in reversed(messages):
        if (
            not isinstance(message, ToolMessage)
            or message.name != FLIGHT_SEARCH_TOOL_NAME
            or message.status == "error"
        ):
            continue
        for payload in iter_tool_payloads(message):
            try:
                guidance = FlightSearchPreparationGuidance.model_validate(payload)
            except (TypeError, ValidationError):
                continue

            requests = [
                request
                for request in (
                    build_airport_input_request(
                        field="origin",
                        resolution=guidance.origin,
                    ),
                    build_airport_input_request(
                        field="destination",
                        resolution=guidance.destination,
                    ),
                )
                if request is not None
            ]
            if requests:
                return TravelClarification(requests=requests)
    return None
