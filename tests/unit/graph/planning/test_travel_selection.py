"""Tests for deterministic flight and hotel selection helpers."""

from datetime import UTC, date, datetime

from app.domain.planning import PendingTravelSelection
from app.graph.planning.travel_selection import (
    flight_date_options,
    flight_date_selection_text,
    match_selection,
    research_for_selection,
    selection_text,
    travel_options,
)
from app.services.planning_research_service import PlanningResearch, ResearchEvidence


def evidence(
    *,
    id: str,
    kind: str,
    name: str,
    source_id: str | None = None,
    alternative_start_date: date | None = None,
    alternative_end_date: date | None = None,
) -> ResearchEvidence:
    return ResearchEvidence(
        id=id,
        kind=kind,
        name=name,
        location="Tokyo",
        description=f"{name} description",
        source_id=source_id,
        alternative_start_date=alternative_start_date,
        alternative_end_date=alternative_end_date,
    )


def research(*items: ResearchEvidence) -> PlanningResearch:
    return PlanningResearch(searched_at=datetime.now(UTC), evidence=items)


def test_travel_options_group_round_trip_and_wrap_hotel_in_tuple() -> None:
    result = research(
        evidence(
            id="offer-1-outbound",
            kind="flight",
            name="Outbound",
            source_id="offer-1",
        ),
        evidence(
            id="offer-1-return",
            kind="flight",
            name="Return",
            source_id="offer-1",
        ),
        evidence(id="hotel-1", kind="hotel", name="Hotel One"),
    )

    flights = travel_options(result, "flight")
    hotels = travel_options(result, "hotel")

    assert flights[0][0] == "offer-1"
    assert tuple(item.id for item in flights[0][1]) == (
        "offer-1-outbound",
        "offer-1-return",
    )
    assert hotels == [("hotel-1", (result.evidence[2],))]


def test_flight_date_options_use_sorted_iso_date_identifiers() -> None:
    later = evidence(
        id="later-outbound",
        kind="flight",
        name="Later",
        source_id="later",
        alternative_start_date=date(2099, 11, 9),
        alternative_end_date=date(2099, 11, 13),
    )
    earlier = evidence(
        id="earlier-outbound",
        kind="flight",
        name="Earlier",
        source_id="earlier",
        alternative_start_date=date(2099, 11, 8),
        alternative_end_date=date(2099, 11, 12),
    )

    options = flight_date_options(research(later, earlier))

    assert [option_id for option_id, _ in options] == [
        "2099-11-08|2099-11-12",
        "2099-11-09|2099-11-13",
    ]


def test_flight_date_text_renders_each_date_and_offer_once() -> None:
    departure = date(2099, 11, 8)
    returning = date(2099, 11, 12)
    options = flight_date_options(
        research(
            evidence(
                id="offer-1-outbound",
                kind="flight",
                name="Air One outbound",
                source_id="offer-1",
                alternative_start_date=departure,
                alternative_end_date=returning,
            ),
            evidence(
                id="offer-1-return",
                kind="flight",
                name="Air One return",
                source_id="offer-1",
                alternative_start_date=departure,
                alternative_end_date=returning,
            ),
        )
    )

    text = flight_date_selection_text(options, language="en")

    assert text.count("Date option 1: 2099-11-08 to 2099-11-12") == 1
    assert text.count("1. Air One outbound") == 1
    assert "return Air One return description" in text


def test_match_selection_supports_number_and_rejects_out_of_range() -> None:
    result = research(
        evidence(id="hotel-1", kind="hotel", name="Hotel One"),
        evidence(id="hotel-2", kind="hotel", name="Hotel Two"),
    )
    pending = PendingTravelSelection(
        kind="hotel",
        option_ids=("hotel-1", "hotel-2"),
        reason="choose",
    )

    assert match_selection("option 2", pending, result) == "hotel-2"
    assert match_selection("option 3", pending, result) is None


def test_research_for_selection_keeps_selected_options_and_places() -> None:
    result = research(
        evidence(
            id="flight-1-outbound",
            kind="flight",
            name="Flight One",
            source_id="flight-1",
        ),
        evidence(
            id="flight-2-outbound",
            kind="flight",
            name="Flight Two",
            source_id="flight-2",
        ),
        evidence(id="hotel-1", kind="hotel", name="Hotel One"),
        evidence(id="hotel-2", kind="hotel", name="Hotel Two"),
        evidence(id="place-1", kind="place", name="Temple"),
    )

    selected = research_for_selection(
        result,
        flight_id="flight-2",
        hotel_id="hotel-1",
    )

    assert tuple(item.id for item in selected.evidence) == (
        "flight-2-outbound",
        "hotel-1",
        "place-1",
    )


def test_selection_text_preserves_readable_spacing() -> None:
    flight = evidence(
        id="flight-1-outbound",
        kind="flight",
        name="Air One",
        source_id="flight-1",
    )

    text = selection_text(
        kind="flight",
        options=[("flight-1", (flight,))],
        language="en",
    )

    assert "Air One; outbound Air One description" in text


def test_roman_urdu_no_results_message_is_not_duplicated() -> None:
    text = selection_text(
        kind="flight",
        options=[],
        language="ur-Latn",
        no_results=True,
    )

    assert text.count("Aap ki tareekh aur route") == 1
