"""Integration coverage for the generated-itinerary response pipeline."""

import asyncio
from collections.abc import Sequence
from datetime import date
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from app.database.models import Conversation, Itinerary, Message, Trip
from app.database.repositories.assistant_runs import AssistantRunClaim
from app.database.repositories.itineraries import ItineraryDetails
from app.domain.itineraries import ItineraryItemType, ItineraryStatus
from app.graph.builder import build_travel_graph
from app.graph.tools import (
    ITINERARY_SUBMISSION_TOOL_NAME,
    create_itinerary_submission_tool,
)
from app.services.conversation_processing_service import (
    ConversationProcessingContext,
    ConversationProcessingService,
    ProcessingStart,
    SaveAssistantReply,
)
from app.services.conversation_service import AcceptedTravelRequest
from app.services.itinerary_service import ItineraryService
from app.services.travel_response_service import TravelResponseService


class ItineraryModelGateway:
    """Return one deterministic final itinerary submission."""

    def __init__(self) -> None:
        self.calls: list[list[BaseMessage]] = []

    async def generate(self, *, messages: Sequence[BaseMessage]) -> AIMessage:
        """Record model context and return a valid two-day handoff."""

        self.calls.append(list(messages))
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": ITINERARY_SUBMISSION_TOOL_NAME,
                    "args": {
                        "summary": "Two-day Lahore culture plan.",
                        "items": [
                            {
                                "day_number": 1,
                                "item_type": "place",
                                "title": "Lahore Fort",
                                "location_name": "Lahore",
                            },
                            {
                                "day_number": 1,
                                "item_type": "meal",
                                "title": "Food Street dinner",
                                "location_name": "Lahore",
                            },
                            {
                                "day_number": 2,
                                "item_type": "place",
                                "title": "Shalimar Gardens",
                                "location_name": "Lahore",
                            },
                        ],
                    },
                    "id": "itinerary-call-1",
                    "type": "tool_call",
                }
            ],
        )


def test_generated_itinerary_flows_from_graph_to_persisted_response() -> None:
    """One accepted trip request should return its persisted itinerary ID."""

    user_id = uuid4()
    trip = Trip(
        id=uuid4(),
        user_id=user_id,
        origin="Karachi",
        destination="Lahore",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 11),
        status="draft",
    )
    conversation = Conversation(
        id=uuid4(),
        user_id=user_id,
        title="Lahore trip",
        locale="en-PK",
    )
    user_message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        trip_id=trip.id,
        client_message_id=uuid4(),
        role="user",
        content="Create a complete itinerary for this trip",
    )
    accepted_request = AcceptedTravelRequest(
        conversation=conversation,
        user_message=user_message,
        trip=trip,
        is_duplicate=False,
    )

    claim = Mock(spec=AssistantRunClaim)
    claim.acquired = True
    claim.claim_token = uuid4()
    claim.run = Mock(id=uuid4())
    processing_start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=accepted_request,
            history=(user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    processing = Mock(spec=ConversationProcessingService)
    processing.start_processing = AsyncMock(return_value=processing_start)
    processing.save_reply = AsyncMock()
    processing.fail_processing = AsyncMock()

    itinerary = Itinerary(
        id=uuid4(),
        trip_id=trip.id,
        source_message_id=user_message.id,
        version=1,
        status=ItineraryStatus.DRAFT.value,
    )
    itineraries = Mock(spec=ItineraryService)
    itineraries.create_generated_draft = AsyncMock(
        return_value=ItineraryDetails(
            itinerary=itinerary,
            items=[],
        )
    )
    itineraries.find_generated_draft = AsyncMock(return_value=None)

    assistant_message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        reply_to_message_id=user_message.id,
        role="assistant",
        content=(
            "Two-day Lahore culture plan. "
            "I created a 2-day itinerary draft for your trip."
        ),
    )

    async def save_reply(**arguments):
        assistant_message.structured_content = arguments.get("structured_content")
        return SaveAssistantReply(
            message=assistant_message,
            is_duplicate=False,
        )

    processing.save_reply.side_effect = save_reply

    gateway = ItineraryModelGateway()
    submission_tool = create_itinerary_submission_tool()
    graph = build_travel_graph(
        model_gateway=gateway,
        tools=[submission_tool],
        max_tool_rounds=2,
    )
    service = TravelResponseService(
        processing_service=processing,
        itinerary_service=itineraries,
        graph=graph,
        assistant_run_lease_seconds=120,
        travel_response_timeout_seconds=75.0,
        max_model_attempts=3,
    )

    result = asyncio.run(
        service.generate_reply(
            user_id=user_id,
            accepted_request=accepted_request,
        )
    )

    assert result.message is assistant_message
    assert result.itinerary_id == itinerary.id
    assert result.is_cached is False
    assert result.error_code is None

    assert len(gateway.calls) == 1
    assert isinstance(gateway.calls[0][0], SystemMessage)
    assert isinstance(gateway.calls[0][1], SystemMessage)
    assert '"destination":"Lahore"' in gateway.calls[0][1].content
    assert '"day_count":2' in gateway.calls[0][1].content

    persistence_call = itineraries.create_generated_draft.await_args
    assert persistence_call.kwargs["trip_id"] == trip.id
    assert persistence_call.kwargs["user_id"] == user_id
    assert persistence_call.kwargs["source_message_id"] == user_message.id
    assert persistence_call.kwargs["commit"] is False
    drafts = persistence_call.kwargs["items"]
    assert [
        (draft.day_number, draft.position, draft.item_type) for draft in drafts
    ] == [
        (1, 1, ItineraryItemType.PLACE),
        (1, 2, ItineraryItemType.MEAL),
        (2, 1, ItineraryItemType.PLACE),
    ]
    save_call = processing.save_reply.await_args
    assert save_call.kwargs["user_id"] == user_id
    assert save_call.kwargs["accepted_request"] is accepted_request
    assert save_call.kwargs["claim"] is claim
    assert save_call.kwargs["content"] == assistant_message.content
    structured_content = save_call.kwargs["structured_content"]
    assert structured_content["type"] == "rich_response"
    assert structured_content["sections"][0]["type"] == "itinerary_preview"
    assert structured_content["sections"][0]["itinerary_id"] == str(itinerary.id)
    assert result.rich_content is not None
    assert result.rich_content.sections[0].itinerary_id == itinerary.id
    processing.fail_processing.assert_not_awaited()
