"""Tests for graph-backed travel response orchestration."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.database.repositories.assistant_runs import AssistantRunClaim
from app.graph.subgraphs.model_gateway import ModelGatewayError
from app.services.conversation_processing_service import (
    ConversationProcessingContext,
    ProcessingStart,
    SaveAssistantReply,
)
from app.services.conversation_service import AcceptedTravelRequest
from app.services.travel_response_service import TravelResponseService


def create_accepted_request() -> AcceptedTravelRequest:
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
        reply_to_message_id=None,
        role="user",
        content="Plan a Lahore trip",
    )
    return AcceptedTravelRequest(
        conversation=conversation,
        user_message=user_message,
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
    *, start: ProcessingStart, graph_result: object | None = None
) -> tuple[TravelResponseService, Mock, Mock]:
    """Create an orchestration service with controllable async collaborators."""

    processing = Mock()
    processing.start_processing = AsyncMock(return_value=start)
    processing.save_reply = AsyncMock()
    processing.fail_processing = AsyncMock()
    graph = Mock()
    graph.ainvoke = AsyncMock(return_value=graph_result)
    service = TravelResponseService(
        processing_service=processing,
        graph=graph,
        assistant_run_lease_seconds=120,
        max_model_attempts=3,
    )
    return service, processing, graph


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


def test_generate_reply_invokes_graph_and_saves_an_owned_response() -> None:
    """An owned claim should generate one reply and persist it atomically."""

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
        content="Three-day Lahore itinerary",
    )
    service, processing, graph = create_service(
        start=start,
        graph_result={"assistant_response": "Three-day Lahore itinerary"},
    )
    processing.save_reply.return_value = SaveAssistantReply(
        message=saved_message,
        is_duplicate=False,
    )

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
    assert graph_input["locale"] == "en-PK"
    assert graph_input["messages"][0].content == "Plan a Lahore trip"
    processing.save_reply.assert_awaited_once_with(
        user_id=request.conversation.user_id,
        accepted_request=request,
        claim=claim,
        content="Three-day Lahore itinerary",
    )
    processing.fail_processing.assert_not_awaited()


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
        error_code="model_unavailable",
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
        error_code="generation_failed",
    )
    processing.save_reply.assert_not_awaited()


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
