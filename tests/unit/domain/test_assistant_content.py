from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.domain.assistant_content import (
    AssistantHotelCard,
    AssistantItineraryPreview,
    AssistantMedia,
    AssistantPlaceCard,
    AssistantRichContent,
)


def valid_rich_content() -> dict[str, object]:
    return {
        "type": "rich_response",
        "schema_version": 1,
        "sections": [
            {
                "type": "place_carousel",
                "id": "suggested-places",
                "title": "Suggested for You",
                "items": [
                    {
                        "id": "gion-district",
                        "name": "Gion District",
                        "location": "Kyoto",
                        "subtitle": "Historic • Cultural",
                        "image": {
                            "url": "https://cdn.roamly.example/places/gion.jpg",
                            "alt_text": "Traditional street in Gion District",
                            "width": 1200,
                            "height": 800,
                        },
                        "latitude": 35.0037,
                        "longitude": 135.7788,
                    }
                ],
            },
            {
                "type": "hotel_carousel",
                "id": "suggested-hotels",
                "title": "Hotels You'll Love",
                "items": [
                    {
                        "id": "hotel-result-1",
                        "name": "The Ritz-Carlton Kyoto",
                        "location": "Kyoto",
                        "category": "Luxury",
                        "rating": 5,
                        "review_score": "9.4",
                        "price": {
                            "amount": "850.00",
                            "currency": "USD",
                            "qualifier": "total",
                        },
                        "image": {
                            "url": "https://cdn.roamly.example/hotels/ritz.jpg",
                            "alt_text": "The Ritz-Carlton Kyoto guest room",
                        },
                        "expires_at": "2026-09-24T10:45:00Z",
                    }
                ],
            },
            {
                "type": "itinerary_preview",
                "id": "generated-itinerary",
                "title": "Your AI-Generated Itinerary",
                "itinerary_id": "fe221e54-80b1-41ee-a43b-fd14f470c08c",
                "summary": {
                    "title": "Japan Adventure",
                    "start_date": "2026-11-07",
                    "end_date": "2026-11-11",
                    "duration_days": 5,
                    "traveler_count": 2,
                    "cities": ["Tokyo", "Kyoto", "Hakone"],
                    "pace": "relaxed",
                },
                "days": [
                    {
                        "day_number": 1,
                        "date": "2026-11-07",
                        "title": "Tokyo",
                        "subtitle": "Arrival and city highlights",
                    }
                ],
            },
        ],
    }


def test_rich_content_parses_discriminated_sections() -> None:
    content = AssistantRichContent.model_validate(valid_rich_content())

    assert content.schema_version == 1
    assert isinstance(content.sections[0].items[0], AssistantPlaceCard)
    assert isinstance(content.sections[1].items[0], AssistantHotelCard)
    assert isinstance(content.sections[2], AssistantItineraryPreview)
    assert content.sections[2].summary.duration_days == 5


@pytest.mark.parametrize("missing_dimension", ["width", "height"])
def test_media_rejects_partial_dimensions(missing_dimension: str) -> None:
    payload = {
        "url": "https://cdn.roamly.example/image.jpg",
        "alt_text": "Destination image",
        "width": 1200,
        "height": 800,
    }
    payload.pop(missing_dimension)

    with pytest.raises(
        ValidationError,
        match="width and height must be provided together",
    ):
        AssistantMedia.model_validate(payload)


@pytest.mark.parametrize("missing_coordinate", ["latitude", "longitude"])
def test_place_rejects_partial_coordinates(missing_coordinate: str) -> None:
    payload = {
        "id": "gion-district",
        "name": "Gion District",
        "location": "Kyoto",
        "latitude": 35.0037,
        "longitude": 135.7788,
    }
    payload.pop(missing_coordinate)

    with pytest.raises(
        ValidationError,
        match="latitude and longitude must be provided together",
    ):
        AssistantPlaceCard.model_validate(payload)


def test_itinerary_rejects_duration_that_disagrees_with_dates() -> None:
    payload = valid_rich_content()
    itinerary = payload["sections"][2]
    itinerary["summary"]["duration_days"] = 4

    with pytest.raises(
        ValidationError,
        match="duration_days must match",
    ):
        AssistantRichContent.model_validate(payload)


@pytest.mark.parametrize("rating", [0, 6])
def test_hotel_rejects_rating_outside_one_to_five(rating: int) -> None:
    payload = valid_rich_content()["sections"][1]["items"][0]
    payload["rating"] = rating

    with pytest.raises(ValidationError):
        AssistantHotelCard.model_validate(payload)


@pytest.mark.parametrize("review_score", [0, 10.1])
def test_hotel_rejects_review_score_outside_one_to_ten(
    review_score: float,
) -> None:
    payload = valid_rich_content()["sections"][1]["items"][0]
    payload["review_score"] = review_score

    with pytest.raises(ValidationError):
        AssistantHotelCard.model_validate(payload)


def test_rich_content_rejects_unknown_section_type() -> None:
    payload = valid_rich_content()
    payload["sections"] = [
        {
            "type": "unsupported_section",
            "id": "unsupported",
            "title": "Unsupported",
            "items": [],
        }
    ]

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        AssistantRichContent.model_validate(payload)


def test_itinerary_day_defaults_are_not_shared() -> None:
    first_payload = deepcopy(valid_rich_content()["sections"][2])
    second_payload = deepcopy(first_payload)
    first_payload.pop("days")
    second_payload.pop("days")

    first = AssistantItineraryPreview.model_validate(first_payload)
    second = AssistantItineraryPreview.model_validate(second_payload)

    first.days.append(
        AssistantItineraryPreview.model_validate(
            valid_rich_content()["sections"][2]
        ).days[0]
    )

    assert len(first.days) == 1
    assert second.days == []


def test_rich_content_serializes_for_json_storage() -> None:
    content = AssistantRichContent.model_validate(valid_rich_content())

    serialized = content.model_dump(mode="json")

    assert serialized["type"] == "rich_response"
    assert serialized["sections"][0]["items"][0]["location"] == "Kyoto"
    assert serialized["sections"][1]["items"][0]["price"] == {
        "amount": "850.00",
        "currency": "USD",
        "qualifier": "total",
    }
