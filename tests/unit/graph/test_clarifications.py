"""Tests for deterministic graph clarification extraction."""

from json import dumps, loads

from langchain_core.messages import AIMessage, ToolMessage

from app.graph.clarifications import extract_travel_clarification


def airport_option(*, code: str, name: str) -> dict[str, object]:
    """Return one normalized airport option dictionary."""

    return {
        "provider_location_id": f"provider-{code.lower()}",
        "iata_code": code,
        "location_type": "airport",
        "name": name,
        "city_name": "London",
        "country_name": "United Kingdom",
        "country_code": "GB",
    }


def flight_guidance_content() -> str:
    """Return one serialized flight airport-guidance result."""

    return dumps(
        {
            "status": "airport_resolution_required",
            "origin": {
                "status": "selection_required",
                "query": "lindon",
                "iata_code": None,
                "options": [
                    airport_option(code="LHR", name="Heathrow Airport"),
                    airport_option(code="LGW", name="Gatwick Airport"),
                ],
            },
            "destination": {
                "status": "resolved",
                "query": "lhaore",
                "iata_code": "LHE",
                "options": [],
            },
            "message": "Airport resolution is required for the origin.",
        }
    )


def test_extract_travel_clarification_maps_airport_choices() -> None:
    """A flight guidance result should become one Flutter-ready request."""

    clarification = extract_travel_clarification(
        [
            AIMessage(content="", tool_calls=[]),
            ToolMessage(
                content=flight_guidance_content(),
                name="search_flights",
                tool_call_id="flight-call",
            ),
        ]
    )

    assert clarification is not None
    assert clarification.type == "airport_selection"
    assert len(clarification.requests) == 1
    request = clarification.requests[0]
    assert request.field == "origin_airport"
    assert request.query == "lindon"
    assert request.status == "selection_required"
    assert [option.iata_code for option in request.options] == ["LHR", "LGW"]


def test_extract_travel_clarification_accepts_text_content_blocks() -> None:
    """Provider-style text blocks should retain structured clarification."""

    clarification = extract_travel_clarification(
        [
            ToolMessage(
                content=[{"type": "text", "text": flight_guidance_content()}],
                name="search_flights",
                tool_call_id="flight-call",
            )
        ]
    )

    assert clarification is not None
    assert clarification.requests[0].field == "origin_airport"


def test_extract_travel_clarification_accepts_mcp_structured_artifact() -> None:
    """MCP structured artifacts should be preferred over display text."""

    clarification = extract_travel_clarification(
        [
            ToolMessage(
                content="Choose one London airport.",
                artifact={
                    "structured_content": {"result": loads(flight_guidance_content())}
                },
                name="search_flights",
                tool_call_id="flight-call",
            )
        ]
    )

    assert clarification is not None
    assert clarification.requests[0].options[0].iata_code == "LHR"


def test_extract_travel_clarification_maps_not_found_endpoint() -> None:
    """A missing destination should request city and country without options."""

    payload = {
        "status": "airport_resolution_required",
        "origin": {
            "status": "resolved",
            "query": "LHR",
            "iata_code": "LHR",
            "options": [],
        },
        "destination": {
            "status": "not_found",
            "query": "unknown place",
            "iata_code": None,
            "options": [],
        },
        "message": "Airport resolution is required for the destination.",
    }

    clarification = extract_travel_clarification(
        [
            ToolMessage(
                content=dumps(payload),
                name="search_flights",
                tool_call_id="flight-call",
            )
        ]
    )

    assert clarification is not None
    request = clarification.requests[0]
    assert request.field == "destination_airport"
    assert request.status == "not_found"
    assert request.options == []
    assert "country or region" in request.question


def test_extract_travel_clarification_ignores_unrelated_or_invalid_messages() -> None:
    """Normal results and malformed content must not create fake input requests."""

    assert (
        extract_travel_clarification(
            [
                ToolMessage(
                    content="not-json",
                    name="search_flights",
                    tool_call_id="flight-call",
                ),
                ToolMessage(
                    content=flight_guidance_content(),
                    name="search_hotels",
                    tool_call_id="hotel-call",
                ),
            ]
        )
        is None
    )
