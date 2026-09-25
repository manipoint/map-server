"""Tests for graph-backed travel response orchestration."""

import asyncio
import logging
from datetime import date
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import ToolMessage

from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.database.models.trip import Trip
from app.database.repositories.assistant_runs import AssistantRunClaim
from app.domain.assistant_content import AssistantRichContent
from app.domain.enums import TravelResponseErrorCode
from app.graph.schemas.itineraries import GeneratedItinerary
from app.graph.subgraphs.model_gateway import (
    FallbackModelGateway,
    ModelGatewayError,
    ModelProvider,
)
from app.observability.langsmith import LangSmithTracerFactory
from app.observability.request_context import get_trace_context
from app.services.conversation_processing_service import (
    ConversationProcessingContext,
    ProcessingStart,
    SaveAssistantReply,
)
from app.services.conversation_service import AcceptedTravelRequest
from app.services.travel_response_service import TravelResponseService


def create_accepted_request(*, trip_id=None) -> AcceptedTravelRequest:
    """Create an in-memory persisted travel request."""

    conversation = Conversation(
        id=uuid4(),
        user_id=uuid4(),
        title="Lahore trip",
        locale="en-PK",
    )
    user_message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        trip_id=trip_id,
        reply_to_message_id=None,
        role="user",
        content="Plan a Lahore trip",
    )
    trip = (
        Trip(
            id=trip_id,
            user_id=conversation.user_id,
            origin="Karachi",
            destination="Lahore",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
            status="draft",
        )
        if trip_id is not None
        else None
    )
    return AcceptedTravelRequest(
        conversation=conversation,
        user_message=user_message,
        trip=trip,
        is_duplicate=False,
    )


def create_claim(*, acquired: bool) -> Mock:
    """Create a processing claim with the minimum service contract."""

    claim = Mock(spec=AssistantRunClaim)
    claim.acquired = acquired
    claim.claim_token = uuid4() if acquired else None
    claim.run = Mock()
    claim.run.id = uuid4()
    return claim


def create_service(
    *,
    start: ProcessingStart,
    graph_result: object | None = None,
    travel_response_timeout_seconds: float = 75.0,
) -> tuple[TravelResponseService, Mock, Mock]:
    """Create an orchestration service with controllable async collaborators."""

    processing = Mock()
    processing.start_processing = AsyncMock(return_value=start)
    processing.save_reply = AsyncMock()
    processing.fail_processing = AsyncMock()
    itineraries = Mock()
    itineraries.create_generated_draft = AsyncMock()
    itineraries.find_generated_draft = AsyncMock(return_value=None)
    graph = Mock()
    graph.ainvoke = AsyncMock(return_value=graph_result)
    service = TravelResponseService(
        processing_service=processing,
        itinerary_service=itineraries,
        graph=graph,
        assistant_run_lease_seconds=120,
        travel_response_timeout_seconds=travel_response_timeout_seconds,
        max_model_attempts=3,
    )
    return service, processing, graph


def generated_itinerary() -> GeneratedItinerary:
    """Build one valid structured itinerary graph result."""

    return GeneratedItinerary.model_validate(
        {
            "summary": "Two-day Lahore plan",
            "items": [
                {
                    "day_number": 1,
                    "item_type": "place",
                    "title": "Lahore Fort",
                },
                {
                    "day_number": 2,
                    "item_type": "meal",
                    "title": "Food Street dinner",
                },
            ],
        }
    )


def airport_clarification_content() -> str:
    """Return one flight-tool clarification encoded as a ToolMessage string."""

    return (
        '{"status":"airport_resolution_required",'
        '"origin":{"status":"selection_required","query":"lindon",'
        '"iata_code":null,"options":['
        '{"provider_location_id":"london-city","iata_code":"LON",'
        '"location_type":"city","name":"London",'
        '"city_name":"London","country_name":"United Kingdom",'
        '"country_code":"GB"},'
        '{"provider_location_id":"stansted","iata_code":"STN",'
        '"location_type":"airport","name":"London Stansted Airport",'
        '"city_name":"London","country_name":"United Kingdom",'
        '"country_code":"GB"}]},'
        '"destination":{"status":"resolved","query":"lhaore",'
        '"iata_code":"LHE","options":[]},'
        '"message":"Airport resolution is required for the origin."}'
    )


def test_generate_reply_returns_a_cached_response_without_model_work() -> None:
    """A persisted reply should avoid another model invocation and DB write."""

    request = create_accepted_request()
    cached_reply = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Existing itinerary",
    )
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(),
            cached_reply=cached_reply,
        ),
        claim=None,
    )
    service, processing, graph = create_service(start=start)

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id, accepted_request=request
        )
    )

    assert result.message is cached_reply
    assert result.is_cached is True
    assert result.is_processing is False
    graph.ainvoke.assert_not_awaited()
    processing.save_reply.assert_not_awaited()
    service.itineraries.find_generated_draft.assert_not_awaited()


def test_generate_reply_restores_cached_structured_clarification() -> None:
    """A duplicate request should return the same airport selection controls."""

    request = create_accepted_request()
    cached_reply = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Select a London airport.",
        structured_content={
            "type": "airport_selection",
            "requests": [
                {
                    "field": "origin_airport",
                    "query": "lindon",
                    "status": "selection_required",
                    "question": "Select an airport for lindon.",
                    "options": [
                        {
                            "provider_location_id": "london-city",
                            "iata_code": "LON",
                            "location_type": "city",
                            "name": "London",
                            "city_name": "London",
                            "country_name": "United Kingdom",
                            "country_code": "GB",
                        },
                        {
                            "provider_location_id": "stansted",
                            "iata_code": "STN",
                            "location_type": "airport",
                            "name": "London Stansted Airport",
                            "city_name": "London",
                            "country_name": "United Kingdom",
                            "country_code": "GB",
                        },
                    ],
                }
            ],
        },
    )
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(),
            cached_reply=cached_reply,
        ),
        claim=None,
    )
    service, _, graph = create_service(start=start)

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id,
            accepted_request=request,
        )
    )

    assert result.clarification is not None
    assert result.clarification.requests[0].options[0].iata_code == "LON"
    graph.ainvoke.assert_not_awaited()


def test_generate_reply_returns_cached_generated_itinerary_id() -> None:
    """A cached retry should retain the structured itinerary navigation target."""

    request = create_accepted_request(trip_id=uuid4())
    cached_reply = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Existing itinerary",
    )
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(),
            cached_reply=cached_reply,
        ),
        claim=None,
    )
    service, _, graph = create_service(start=start)
    itinerary_id = uuid4()
    generated_draft = Mock()
    generated_draft.itinerary = Mock(id=itinerary_id)
    service.itineraries.find_generated_draft.return_value = generated_draft

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id,
            accepted_request=request,
        )
    )

    assert result.message is cached_reply
    assert result.itinerary_id == itinerary_id
    service.itineraries.find_generated_draft.assert_awaited_once_with(
        source_message_id=request.user_message.id,
        user_id=request.conversation.user_id,
    )
    graph.ainvoke.assert_not_awaited()


def test_generate_reply_reports_processing_when_another_worker_owns_claim() -> None:
    """An active unowned lease must not trigger a duplicate model request."""

    request = create_accepted_request()
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=create_claim(acquired=False),
    )
    service, processing, graph = create_service(start=start)

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id, accepted_request=request
        )
    )

    assert result.message is None
    assert result.is_cached is False
    assert result.is_processing is True
    graph.ainvoke.assert_not_awaited()
    processing.save_reply.assert_not_awaited()


def test_generate_reply_reports_exhausted_attempts_without_model_work() -> None:
    """A failed run at the retry cap should not spend another model request."""

    request = create_accepted_request()
    claim = create_claim(acquired=False)
    claim.run.status = "failed"
    claim.run.attempt_count = 3
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, graph = create_service(start=start)

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id,
            accepted_request=request,
        )
    )

    assert result.message is None
    assert result.is_cached is False
    assert result.is_processing is False
    assert result.error_code is TravelResponseErrorCode.ATTEMPTS_EXHAUSTED
    graph.ainvoke.assert_not_awaited()
    processing.save_reply.assert_not_awaited()


def test_generate_reply_invokes_graph_and_saves_an_owned_response() -> None:
    """An owned claim should generate one reply and persist it atomically."""

    trip_id = uuid4()
    request = create_accepted_request(trip_id=trip_id)
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    saved_message = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Three-day Lahore itinerary",
    )
    service, processing, graph = create_service(
        start=start,
        graph_result={"assistant_response": "Three-day Lahore itinerary"},
    )

    async def save_reply(**arguments):
        saved_message.structured_content = arguments.get("structured_content")
        return SaveAssistantReply(
            message=saved_message,
            is_duplicate=False,
        )

    processing.save_reply.side_effect = save_reply

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id, accepted_request=request
        )
    )

    assert result.message is saved_message
    assert result.is_cached is False
    assert result.is_processing is False
    graph.ainvoke.assert_awaited_once()
    graph_input = graph.ainvoke.await_args.args[0]
    graph_config = graph.ainvoke.await_args.kwargs["config"]
    assert graph_config["run_id"] != request.user_message.client_message_id
    assert graph_config["metadata"]["trace_id"] == str(graph_config["run_id"])
    assert graph_config["metadata"]["conversation_id"] == str(request.conversation.id)
    assert graph_config["metadata"]["client_message_id"] == str(
        request.user_message.client_message_id
    )
    assert graph_input["locale"] == "en-PK"
    assert graph_input["trip_id"] == trip_id
    assert graph_input["trip_context"].destination == "Lahore"
    assert graph_input["trip_context"].day_count == 3
    assert graph_input["messages"][0].content == "Plan a Lahore trip"
    processing.save_reply.assert_awaited_once_with(
        user_id=request.conversation.user_id,
        accepted_request=request,
        claim=claim,
        content="Three-day Lahore itinerary",
    )
    processing.fail_processing.assert_not_awaited()
    service.itineraries.create_generated_draft.assert_not_awaited()


def test_generate_reply_persists_graph_airport_clarification() -> None:
    """Structured tool guidance should be stored and returned with the reply."""

    request = create_accepted_request()
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    saved_message = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Select a London airport.",
    )
    tool_message = ToolMessage(
        content=airport_clarification_content(),
        name="search_flights",
        tool_call_id="flight-call",
    )
    service, processing, _ = create_service(
        start=start,
        graph_result={
            "messages": [tool_message],
            "assistant_response": "Select a London airport.",
        },
    )

    async def save_reply(**arguments):
        saved_message.structured_content = arguments["structured_content"]
        return SaveAssistantReply(message=saved_message, is_duplicate=False)

    processing.save_reply.side_effect = save_reply

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id,
            accepted_request=request,
        )
    )

    assert result.clarification is not None
    clarification = result.clarification
    assert clarification.requests[0].field == "origin_airport"
    assert clarification.requests[0].options[0].iata_code == "LON"
    structured_content = processing.save_reply.await_args.kwargs["structured_content"]
    assert structured_content["type"] == "airport_selection"


def test_generate_reply_persists_generated_itinerary_before_reply() -> None:
    """A structured graph result should create one source-linked draft."""

    trip_id = uuid4()
    request = create_accepted_request(trip_id=trip_id)
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    saved_message = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Two-day Lahore plan",
    )
    service, processing, _ = create_service(
        start=start,
        graph_result={
            "assistant_response": "Two-day Lahore plan",
            "generated_itinerary": generated_itinerary(),
        },
    )

    async def save_reply(**arguments):
        saved_message.structured_content = arguments.get("structured_content")
        return SaveAssistantReply(
            message=saved_message,
            is_duplicate=False,
        )

    processing.save_reply.side_effect = save_reply
    itinerary_id = uuid4()
    generated_draft = Mock()
    generated_draft.itinerary = Mock(id=itinerary_id)
    service.itineraries.create_generated_draft.return_value = generated_draft

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id,
            accepted_request=request,
        )
    )

    assert result.message is saved_message
    assert result.itinerary_id == itinerary_id
    call = service.itineraries.create_generated_draft.await_args
    assert call.kwargs["trip_id"] == trip_id
    assert call.kwargs["user_id"] == request.conversation.user_id
    assert call.kwargs["source_message_id"] == request.user_message.id
    assert call.kwargs["commit"] is False
    drafts = call.kwargs["items"]
    assert [(item.day_number, item.position) for item in drafts] == [
        (1, 1),
        (2, 1),
    ]
    structured_content = processing.save_reply.await_args.kwargs["structured_content"]
    assert structured_content["type"] == "rich_response"
    assert structured_content["schema_version"] == 1
    assert structured_content["sections"][0]["type"] == "itinerary_preview"
    assert structured_content["sections"][0]["itinerary_id"] == str(itinerary_id)
    assert result.rich_content is not None
    preview = result.rich_content.sections[0]
    assert preview.itinerary_id == itinerary_id
    processing.save_reply.assert_awaited_once()


def test_generate_reply_rejects_generated_itinerary_without_trip_context() -> None:
    """A model must not persist an itinerary from standalone conversation."""

    request = create_accepted_request()
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, _ = create_service(
        start=start,
        graph_result={
            "assistant_response": "Invalid standalone itinerary",
            "generated_itinerary": generated_itinerary(),
        },
    )

    with pytest.raises(RuntimeError, match="requires trip context"):
        asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    service.itineraries.create_generated_draft.assert_not_awaited()
    processing.save_reply.assert_not_awaited()
    processing.fail_processing.assert_awaited_once_with(
        claim=claim,
        error_code=TravelResponseErrorCode.GENERATION_FAILED,
    )


def test_generate_reply_fails_claim_when_itinerary_persistence_fails() -> None:
    """A draft failure should prevent an assistant completion from being saved."""

    request = create_accepted_request(trip_id=uuid4())
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, _ = create_service(
        start=start,
        graph_result={
            "assistant_response": "Generated itinerary",
            "generated_itinerary": generated_itinerary(),
        },
    )
    service.itineraries.create_generated_draft.side_effect = RuntimeError(
        "draft persistence failed"
    )

    with pytest.raises(RuntimeError, match="draft persistence failed"):
        asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    processing.save_reply.assert_not_awaited()
    processing.fail_processing.assert_awaited_once_with(
        claim=claim,
        error_code=TravelResponseErrorCode.GENERATION_FAILED,
    )


def test_generate_reply_marks_an_owned_claim_failed_when_model_is_unavailable() -> None:
    """A gateway failure should record a safe retryable error code."""

    request = create_accepted_request()
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, graph = create_service(start=start)
    graph.ainvoke.side_effect = ModelGatewayError("provider detail")

    with pytest.raises(ModelGatewayError, match="provider detail"):
        asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    processing.fail_processing.assert_awaited_once_with(
        claim=claim,
        error_code=TravelResponseErrorCode.PROVIDER_ERROR,
    )
    processing.save_reply.assert_not_awaited()


def test_generate_reply_marks_an_owned_claim_failed_for_an_unexpected_error() -> None:
    """Unexpected graph errors should use a safe internal failure code."""

    request = create_accepted_request()
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, graph = create_service(start=start)
    graph.ainvoke.side_effect = RuntimeError("unexpected graph detail")

    with pytest.raises(RuntimeError, match="unexpected graph detail"):
        asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    processing.fail_processing.assert_awaited_once_with(
        claim=claim,
        error_code=TravelResponseErrorCode.GENERATION_FAILED,
    )
    processing.save_reply.assert_not_awaited()


def test_generate_reply_times_out_graph_before_its_lease_can_expire() -> None:
    """One end-to-end deadline should stop a slow model/tool graph run."""

    request = create_accepted_request()
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, graph = create_service(
        start=start,
        travel_response_timeout_seconds=0.01,
    )

    async def wait_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    graph.ainvoke.side_effect = wait_forever

    with pytest.raises(TimeoutError):
        asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    processing.fail_processing.assert_awaited_once_with(
        claim=claim,
        error_code=TravelResponseErrorCode.GENERATION_FAILED,
    )
    processing.save_reply.assert_not_awaited()


def test_generate_reply_deadline_also_bounds_reply_persistence() -> None:
    """A slow completion transaction must not outlive the response deadline."""

    request = create_accepted_request()
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, graph = create_service(
        start=start,
        graph_result={"assistant_response": "Bounded reply"},
        travel_response_timeout_seconds=0.01,
    )

    async def wait_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    processing.save_reply.side_effect = wait_forever

    with pytest.raises(TimeoutError):
        asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    graph.ainvoke.assert_awaited_once()
    processing.save_reply.assert_awaited_once()
    processing.fail_processing.assert_awaited_once_with(
        claim=claim,
        error_code=TravelResponseErrorCode.GENERATION_FAILED,
    )


def test_generate_reply_does_not_fail_a_claim_when_cancelled() -> None:
    """Cancellation leaves the lease intact for safe expiry and later reclaim."""

    request = create_accepted_request()
    claim = create_claim(acquired=True)
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(request.user_message,),
            cached_reply=None,
        ),
        claim=claim,
    )
    service, processing, graph = create_service(start=start)
    graph.ainvoke.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    processing.fail_processing.assert_not_awaited()
    processing.save_reply.assert_not_awaited()


def test_repeated_graph_executions_use_independent_tracers() -> None:
    request = create_accepted_request()
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request, history=(request.user_message,), cached_reply=None
        ),
        claim=create_claim(acquired=True),
    )
    service, _, graph = create_service(start=start)
    client = Mock()
    service.langsmith_tracer_factory = LangSmithTracerFactory(
        client=client, project_name="test-project", app_env="test"
    )
    graph.ainvoke.side_effect = RuntimeError("synthetic failure")

    async def exercise() -> None:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="synthetic failure"):
                await service.generate_reply(
                    user_id=request.conversation.user_id, accepted_request=request
                )
            assert get_trace_context() is None

    asyncio.run(exercise())
    first, second = [
        call.kwargs["config"]["callbacks"][0] for call in graph.ainvoke.await_args_list
    ]
    assert first is not second
    assert first.client is second.client is client
    assert first.order_map is not second.order_map
    assert first.run_map is not second.run_map


@pytest.mark.parametrize("outcome", ["timeout", "cancelled"])
def test_graph_and_provider_record_deadline_and_cancellation(outcome, caplog) -> None:
    request = create_accepted_request()
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request, history=(request.user_message,), cached_reply=None
        ),
        claim=create_claim(acquired=True),
    )
    service, processing, graph = create_service(
        start=start,
        travel_response_timeout_seconds=0.01 if outcome == "timeout" else 75.0,
    )
    provider = Mock()
    fallback = Mock()
    fallback.ainvoke = AsyncMock()

    async def wait_for_cancellation(_messages):
        if outcome == "cancelled":
            asyncio.current_task().cancel()
        await asyncio.Event().wait()

    provider.ainvoke = wait_for_cancellation
    gateway = FallbackModelGateway(
        [ModelProvider("primary", provider), ModelProvider("fallback", fallback)]
    )

    async def invoke_graph(graph_input, **_kwargs):
        return await gateway.generate(messages=graph_input["messages"])

    graph.ainvoke.side_effect = invoke_graph

    async def exercise():
        expected_error = (
            TimeoutError if outcome == "timeout" else asyncio.CancelledError
        )
        with pytest.raises(expected_error):
            await service.generate_reply(
                user_id=request.conversation.user_id, accepted_request=request
            )
        assert get_trace_context() is None

    with caplog.at_level(logging.INFO):
        asyncio.run(exercise())

    metrics = {
        record.metric_name: record
        for record in caplog.records
        if getattr(record, "event", None) == "application_metric"
    }
    assert metrics["model_provider_attempts"].metric_labels["outcome"] == outcome
    assert metrics["travel_graph_runs"].metric_labels["outcome"] == outcome
    assert metrics["model_provider_duration_ms"].metric_value >= 0
    assert metrics["travel_graph_duration_ms"].metric_value >= 0
    fallback.ainvoke.assert_not_awaited()
    if outcome == "cancelled":
        processing.fail_processing.assert_not_awaited()
    else:
        processing.fail_processing.assert_awaited_once()


def test_generate_reply_restores_cached_rich_content() -> None:
    """A duplicate request should restore the same rich response."""

    request = create_accepted_request()

    rich_content = AssistantRichContent.model_validate(
        {
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
                        }
                    ],
                }
            ],
        }
    )

    cached_reply = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Here are some places you may enjoy.",
        structured_content=rich_content.model_dump(mode="json"),
    )

    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(),
            cached_reply=cached_reply,
        ),
        claim=None,
    )

    service, processing, graph = create_service(start=start)

    result = asyncio.run(
        service.generate_reply(
            user_id=request.conversation.user_id,
            accepted_request=request,
        )
    )

    assert result.message is cached_reply
    assert result.is_cached is True
    assert result.clarification is None
    assert result.rich_content == rich_content

    graph.ainvoke.assert_not_awaited()
    processing.save_reply.assert_not_awaited()


def test_generate_reply_ignores_invalid_cached_rich_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid stored content must not prevent the text response."""

    request = create_accepted_request()

    cached_reply = Message(
        id=uuid4(),
        conversation_id=request.conversation.id,
        client_message_id=None,
        reply_to_message_id=request.user_message.id,
        role="assistant",
        content="Your recommendations are ready.",
        structured_content={
            "type": "rich_response",
            "schema_version": 1,
            "sections": [],
        },
    )

    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=request,
            history=(),
            cached_reply=cached_reply,
        ),
        claim=None,
    )

    service, _, _ = create_service(start=start)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(
            service.generate_reply(
                user_id=request.conversation.user_id,
                accepted_request=request,
            )
        )

    assert result.message is cached_reply
    assert result.clarification is None
    assert result.rich_content is None
    assert "structured content is invalid" in caplog.text
