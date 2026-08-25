"""Tests for cost-bounded Tavily place-search transport schemas."""

import pytest
from pydantic import ValidationError

from app.providers.places.tavily_schemas import (
    TavilyPlaceSearchItem,
    TavilyPlaceSearchRequest,
    TavilyPlaceSearchResponse,
)


def create_item(**overrides: object) -> dict[str, object]:
    """Create one minimal Tavily result payload."""

    values: dict[str, object] = {
        "title": "British Museum",
        "url": "https://www.britishmuseum.org/visit",
        "content": "Visitor information for the British Museum in London.",
        "score": 0.91,
    }
    values.update(overrides)
    return values


def test_tavily_request_uses_cost_bounded_defaults() -> None:
    """Default transport options should avoid expensive generated payloads."""

    request = TavilyPlaceSearchRequest(query=" museums in London ")

    assert request.model_dump(mode="json") == {
        "query": "museums in London",
        "topic": "general",
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"search_depth": "advanced"},
        {"include_answer": True},
        {"include_raw_content": True},
        {"include_images": True},
        {"auto_parameters": True},
        {"max_results": 0},
        {"max_results": 11},
        {"unexpected": "value"},
    ],
)
def test_tavily_request_rejects_unbounded_options(
    overrides: dict[str, object],
) -> None:
    """Callers should not override fixed cost and response-size controls."""

    with pytest.raises(ValidationError):
        TavilyPlaceSearchRequest(query="museums in London", **overrides)


def test_tavily_item_validates_core_fields_and_ignores_future_metadata() -> None:
    """Relevant result fields should parse despite harmless provider additions."""

    item = TavilyPlaceSearchItem.model_validate(
        create_item(
            title=" British Museum ",
            content=" Visitor information. ",
            favicon="https://example.com/favicon.ico",
        )
    )

    assert item.title == "British Museum"
    assert item.content == "Visitor information."
    assert str(item.url) == "https://www.britishmuseum.org/visit"
    assert item.score == 0.91


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": " "},
        {"url": "not-a-url"},
        {"content": " "},
        {"score": -0.01},
        {"score": 1.01},
    ],
)
def test_tavily_item_rejects_invalid_provider_fields(
    overrides: dict[str, object],
) -> None:
    """Malformed evidence should not reach normalized place mapping."""

    with pytest.raises(ValidationError):
        TavilyPlaceSearchItem.model_validate(create_item(**overrides))


def test_tavily_response_accepts_empty_results_and_ignores_metadata() -> None:
    """An empty provider result list should remain a valid search outcome."""

    response = TavilyPlaceSearchResponse.model_validate(
        {
            "query": " museums in London ",
            "results": [],
            "request_id": " request-123 ",
            "response_time": "0.42",
            "usage": {"credits": 1},
        }
    )

    assert response.query == "museums in London"
    assert response.results == []
    assert response.request_id == "request-123"


def test_tavily_response_validates_nested_results() -> None:
    """Valid provider evidence should become typed bounded items."""

    response = TavilyPlaceSearchResponse(
        query="museums in London",
        results=[create_item()],
        request_id="request-123",
    )

    assert response.results[0].title == "British Museum"


def test_tavily_response_rejects_more_than_provider_maximum() -> None:
    """Unexpectedly large responses should be rejected before mapping."""

    with pytest.raises(ValidationError):
        TavilyPlaceSearchResponse(
            query="museums in London",
            results=[create_item() for _ in range(21)],
        )
