"""Tests for preparing persisted conversation context."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.database.repositories.assistant_runs import (
    AssistantRunClaim,
    AssistantRunRepository,
)
from app.database.repositories.messages import MessageRepository
from app.domain.enums import AssistantRunStatus
from app.services.conversation_processing_service import (
    ConversationProcessingContext,
    ConversationProcessingService,
    ProcessingStart,
)
from app.services.conversation_service import AcceptedTravelRequest


def create_accepted_request(*, is_duplicate: bool = False) -> AcceptedTravelRequest:
    """Create an in-memory accepted travel request."""

    conversation = Conversation(
        id=uuid4(),
        user_id=uuid4(),
        title="Lahore trip",
        locale="en",
    )
    user_message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        reply_to_message_id=None,
        role="user",
        content="Plan a trip to Lahore",
    )
    return AcceptedTravelRequest(
        conversation=conversation,
        user_message=user_message,
        is_duplicate=is_duplicate,
    )


def create_service(
    *, history_limit: int = 20
) -> tuple[ConversationProcessingService, Mock, Mock, Mock]:
    """Create a processing service with a mocked message repository."""

    session = Mock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    messages = Mock(spec=MessageRepository)
    messages.get_assistant_reply = AsyncMock()
    messages.list_recent_by_conversation = AsyncMock()
    messages.create_assistant_message = AsyncMock()
    runs = Mock(spec=AssistantRunRepository)
    runs.acquire_claim = AsyncMock()
    runs.complete_run = AsyncMock()
    runs.fail_run = AsyncMock()
    service = ConversationProcessingService(
        session=session,
        history_limit=history_limit,
        message_repository=messages,
        assistant_run_repository=runs,
    )
    return service, session, messages, runs


def create_processing_claim(*, acquired: bool = True) -> Mock:
    """Create an in-memory processing claim owned by one worker."""

    claim = Mock(spec=AssistantRunClaim)
    claim.acquired = acquired
    claim.claim_token = uuid4() if acquired else None
    claim.run = Mock()
    claim.run.id = uuid4()
    return claim


def test_processing_context_requires_generation_without_cached_reply() -> None:
    """A context without a persisted assistant response needs generation."""

    context = ConversationProcessingContext(
        accepted_request=create_accepted_request(),
        history=(),
        cached_reply=None,
    )

    assert context.should_generate_reply is True


def test_processing_context_does_not_generate_with_cached_reply() -> None:
    """A persisted assistant response should be reused."""

    accepted_request = create_accepted_request(is_duplicate=True)
    reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Here is your itinerary.",
    )
    context = ConversationProcessingContext(
        accepted_request=accepted_request,
        history=(),
        cached_reply=reply,
    )

    assert context.should_generate_reply is False


def test_processing_start_requires_an_owned_claim_without_a_cached_reply() -> None:
    """Only the worker that owns a lease may invoke the travel model."""

    context = ConversationProcessingContext(
        accepted_request=create_accepted_request(),
        history=(),
        cached_reply=None,
    )
    owned_claim = create_processing_claim()
    unowned_claim = create_processing_claim(acquired=False)

    assert ProcessingStart(context=context, claim=owned_claim).should_invoke_model
    assert not ProcessingStart(context=context, claim=unowned_claim).should_invoke_model


def test_processing_start_never_invokes_the_model_for_a_cached_reply() -> None:
    """A persisted reply takes priority even if a claim object is supplied."""

    accepted_request = create_accepted_request()
    cached_reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Existing itinerary",
    )
    context = ConversationProcessingContext(
        accepted_request=accepted_request,
        history=(),
        cached_reply=cached_reply,
    )

    assert not ProcessingStart(
        context=context,
        claim=create_processing_claim(),
    ).should_invoke_model


def test_processing_start_detects_a_failed_run_with_no_attempts_remaining() -> None:
    """A failed run at the retry cap must not be reported as active processing."""

    claim = create_processing_claim(acquired=False)
    claim.run.status = AssistantRunStatus.FAILED
    claim.run.attempt_count = 3
    start = ProcessingStart(
        context=ConversationProcessingContext(
            accepted_request=create_accepted_request(),
            history=(),
            cached_reply=None,
        ),
        claim=claim,
    )

    assert start.is_attempts_exhausted(max_attempts=3)
    assert not start.is_attempts_exhausted(max_attempts=4)


def test_prepare_returns_cached_reply_without_loading_history() -> None:
    """A completed request should avoid history loading and model work."""

    service, _, messages, _ = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request(is_duplicate=True)
    reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Here is your itinerary.",
    )
    messages.get_assistant_reply.return_value = reply

    result = asyncio.run(
        service.prepare(user_id=user_id, accepted_request=accepted_request)
    )

    assert result.accepted_request is accepted_request
    assert result.history == ()
    assert result.cached_reply is reply
    assert result.should_generate_reply is False
    messages.get_assistant_reply.assert_awaited_once_with(
        user_id=user_id,
        reply_to_message_id=accepted_request.user_message.id,
    )
    messages.list_recent_by_conversation.assert_not_awaited()


def test_prepare_loads_bounded_history_when_reply_is_missing() -> None:
    """A new request should load limited chronological model context."""

    service, _, messages, _ = create_service(history_limit=12)
    user_id = uuid4()
    accepted_request = create_accepted_request()
    history = [accepted_request.user_message]
    messages.get_assistant_reply.return_value = None
    messages.list_recent_by_conversation.return_value = history

    result = asyncio.run(
        service.prepare(user_id=user_id, accepted_request=accepted_request)
    )

    assert result.accepted_request is accepted_request
    assert result.history == tuple(history)
    assert result.cached_reply is None
    assert result.should_generate_reply is True
    messages.list_recent_by_conversation.assert_awaited_once_with(
        conversation_id=accepted_request.conversation.id,
        user_id=user_id,
        limit=12,
    )


def test_start_processing_returns_a_cached_reply_without_claiming() -> None:
    """A completed request should be returned without a new database lease."""

    service, session, messages, runs = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request()
    cached_reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Existing itinerary",
    )
    messages.get_assistant_reply.return_value = cached_reply

    result = asyncio.run(
        service.start_processing(
            user_id=user_id,
            accepted_request=accepted_request,
            lease_seconds=120,
            max_attempts=3,
        )
    )

    assert result.context.cached_reply is cached_reply
    assert result.claim is None
    assert not result.should_invoke_model
    messages.list_recent_by_conversation.assert_not_awaited()
    runs.acquire_claim.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_start_processing_returns_owned_claim_and_bounded_history() -> None:
    """A new request should load context then commit its model-processing lease."""

    service, session, messages, runs = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request()
    claim = create_processing_claim()
    messages.get_assistant_reply.return_value = None
    messages.list_recent_by_conversation.return_value = [accepted_request.user_message]
    runs.acquire_claim.return_value = claim

    result = asyncio.run(
        service.start_processing(
            user_id=user_id,
            accepted_request=accepted_request,
            lease_seconds=120,
            max_attempts=3,
        )
    )

    assert result.context.history == (accepted_request.user_message,)
    assert result.context.cached_reply is None
    assert result.claim is claim
    assert result.should_invoke_model
    runs.acquire_claim.assert_awaited_once_with(
        user_message_id=accepted_request.user_message.id,
        lease_duration=timedelta(seconds=120),
        max_attempts=3,
    )
    session.commit.assert_awaited_once_with()


def test_start_processing_returns_an_unowned_active_claim() -> None:
    """A second worker must not invoke the model when another owns the run."""

    service, _, messages, runs = create_service()
    accepted_request = create_accepted_request()
    claim = create_processing_claim(acquired=False)
    messages.get_assistant_reply.return_value = None
    messages.list_recent_by_conversation.return_value = []
    runs.acquire_claim.return_value = claim

    result = asyncio.run(
        service.start_processing(
            user_id=uuid4(),
            accepted_request=accepted_request,
            lease_seconds=120,
            max_attempts=3,
        )
    )

    assert result.claim is claim
    assert not result.should_invoke_model


def test_start_processing_does_not_claim_when_context_preparation_fails() -> None:
    """History lookup failures should stop before any processing lease is acquired."""

    service, _, messages, runs = create_service()
    messages.get_assistant_reply.return_value = None
    messages.list_recent_by_conversation.side_effect = RuntimeError(
        "database unavailable"
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.start_processing(
                user_id=uuid4(),
                accepted_request=create_accepted_request(),
                lease_seconds=120,
                max_attempts=3,
            )
        )

    runs.acquire_claim.assert_not_awaited()


@pytest.mark.parametrize("history_limit", [0, -1])
def test_service_rejects_a_non_positive_history_limit(history_limit: int) -> None:
    """History configuration must always produce a bounded non-empty query."""

    session = Mock(spec=AsyncSession)

    with pytest.raises(ValueError, match="history_limit must be greater than zero"):
        ConversationProcessingService(
            session=session,
            history_limit=history_limit,
        )


def test_save_reply_reuses_an_existing_canonical_reply() -> None:
    """An already completed request should not create another reply."""

    service, session, messages, runs = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request(is_duplicate=True)
    existing_reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Existing itinerary",
    )
    messages.get_assistant_reply.return_value = existing_reply

    result = asyncio.run(
        service.save_reply(
            user_id=user_id,
            accepted_request=accepted_request,
            claim=create_processing_claim(),
            content="Newly generated itinerary",
        )
    )

    assert result.message is existing_reply
    assert result.is_duplicate is True
    messages.get_assistant_reply.assert_awaited_once_with(
        user_id=user_id,
        reply_to_message_id=accepted_request.user_message.id,
    )
    messages.create_assistant_message.assert_not_awaited()
    runs.complete_run.assert_awaited_once()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_save_reply_creates_normalized_content_and_commits() -> None:
    """A new assistant reply should be trimmed, persisted, and committed."""

    service, session, messages, runs = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request()
    assistant_message = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Generated itinerary",
    )
    messages.get_assistant_reply.return_value = None
    messages.create_assistant_message.return_value = assistant_message
    claim = create_processing_claim()

    result = asyncio.run(
        service.save_reply(
            user_id=user_id,
            accepted_request=accepted_request,
            claim=claim,
            content="  Generated itinerary\n",
        )
    )

    assert result.message is assistant_message
    assert result.is_duplicate is False
    messages.create_assistant_message.assert_awaited_once_with(
        conversation_id=accepted_request.conversation.id,
        reply_to_message_id=accepted_request.user_message.id,
        content="Generated itinerary",
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    runs.complete_run.assert_awaited_once_with(
        run_id=claim.run.id,
        claim_token=claim.claim_token,
        assistant_message_id=assistant_message.id,
    )


def test_save_reply_recovers_the_winner_after_a_unique_race() -> None:
    """A concurrent unique conflict should return the reply committed first."""

    service, session, messages, runs = create_service()
    user_id = uuid4()
    accepted_request = create_accepted_request()
    winning_reply = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Winning itinerary",
    )
    integrity_error = IntegrityError("INSERT", {}, Exception("duplicate reply"))
    messages.get_assistant_reply.side_effect = [None, winning_reply]
    messages.create_assistant_message.side_effect = integrity_error

    result = asyncio.run(
        service.save_reply(
            user_id=user_id,
            accepted_request=accepted_request,
            claim=create_processing_claim(),
            content="Losing itinerary",
        )
    )

    assert result.message is winning_reply
    assert result.is_duplicate is True
    assert messages.get_assistant_reply.await_count == 2
    session.rollback.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
    runs.complete_run.assert_awaited_once()


def test_save_reply_reraises_an_unrelated_integrity_error() -> None:
    """An integrity failure without a competing reply must not be hidden."""

    service, session, messages, _ = create_service()
    accepted_request = create_accepted_request()
    integrity_error = IntegrityError("INSERT", {}, Exception("other constraint"))
    messages.get_assistant_reply.side_effect = [None, None]
    messages.create_assistant_message.side_effect = integrity_error

    with pytest.raises(IntegrityError) as raised_error:
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=accepted_request,
                claim=create_processing_claim(),
                content="Generated itinerary",
            )
        )

    assert raised_error.value is integrity_error
    assert session.rollback.await_count == 2
    session.commit.assert_not_awaited()


def test_save_reply_rolls_back_an_unexpected_error() -> None:
    """Unexpected persistence failures should leave the transaction reusable."""

    service, session, messages, _ = create_service()
    accepted_request = create_accepted_request()
    messages.get_assistant_reply.return_value = None
    messages.create_assistant_message.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=accepted_request,
                claim=create_processing_claim(),
                content="Generated itinerary",
            )
        )

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_save_reply_rejects_blank_content_without_database_work(content: str) -> None:
    """A blank model response should fail before opening a transaction."""

    service, session, messages, _ = create_service()

    with pytest.raises(
        ValueError,
        match="assistant reply content must not be blank",
    ):
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=create_accepted_request(),
                claim=create_processing_claim(),
                content=content,
            )
        )

    messages.get_assistant_reply.assert_not_awaited()
    messages.create_assistant_message.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_save_reply_rolls_back_when_a_claim_is_no_longer_owned() -> None:
    """A stale worker must not commit its flushed assistant message."""

    service, session, messages, runs = create_service()
    accepted_request = create_accepted_request()
    assistant_message = Message(
        id=uuid4(),
        conversation_id=accepted_request.conversation.id,
        client_message_id=None,
        reply_to_message_id=accepted_request.user_message.id,
        role="assistant",
        content="Generated itinerary",
    )
    claim = create_processing_claim()
    messages.get_assistant_reply.return_value = None
    messages.create_assistant_message.return_value = assistant_message
    runs.complete_run.return_value = None

    with pytest.raises(
        RuntimeError,
        match="Assistant processing claim is no longer owned",
    ):
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=accepted_request,
                claim=claim,
                content="Generated itinerary",
            )
        )

    runs.complete_run.assert_awaited_once_with(
        run_id=claim.run.id,
        claim_token=claim.claim_token,
        assistant_message_id=assistant_message.id,
    )
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


@pytest.mark.parametrize("acquired, claim_token", [(False, None), (True, None)])
def test_save_reply_rejects_an_unowned_or_tokenless_claim(
    acquired: bool,
    claim_token,
) -> None:
    """Only a valid owned lease may persist a generated assistant reply."""

    service, session, messages, runs = create_service()
    claim = create_processing_claim(acquired=acquired)
    claim.claim_token = claim_token

    expected_message = (
        "assistant processing claim was not acquired"
        if not acquired
        else "acquired assistant processing claim must have a token"
    )

    with pytest.raises(ValueError, match=expected_message):
        asyncio.run(
            service.save_reply(
                user_id=uuid4(),
                accepted_request=create_accepted_request(),
                claim=claim,
                content="Generated itinerary",
            )
        )

    messages.get_assistant_reply.assert_not_awaited()
    messages.create_assistant_message.assert_not_awaited()
    runs.complete_run.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_acquire_processing_claim_forwards_settings_and_commits() -> None:
    """A newly acquired lease must be committed before model processing starts."""

    service, session, _, runs = create_service()
    accepted_request = create_accepted_request()
    claim = Mock(spec=AssistantRunClaim)
    claim.acquired = True
    runs.acquire_claim.return_value = claim

    result = asyncio.run(
        service.acquire_processing_claim(
            accepted_request=accepted_request,
            lease_seconds=120,
            max_attempts=3,
        )
    )

    assert result is claim
    runs.acquire_claim.assert_awaited_once_with(
        user_message_id=accepted_request.user_message.id,
        lease_duration=timedelta(seconds=120),
        max_attempts=3,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_acquire_processing_claim_commits_when_another_worker_owns_it() -> None:
    """A read-only existing-run result should also close its transaction."""

    service, session, _, runs = create_service()
    claim = Mock(spec=AssistantRunClaim)
    claim.acquired = False
    runs.acquire_claim.return_value = claim

    result = asyncio.run(
        service.acquire_processing_claim(
            accepted_request=create_accepted_request(),
            lease_seconds=120,
            max_attempts=3,
        )
    )

    assert result is claim
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_acquire_processing_claim_rolls_back_a_repository_failure() -> None:
    """Claim acquisition errors must leave the short-lived session reusable."""

    service, session, _, runs = create_service()
    runs.acquire_claim.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.acquire_processing_claim(
                accepted_request=create_accepted_request(),
                lease_seconds=120,
                max_attempts=3,
            )
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_acquire_processing_claim_rolls_back_a_commit_failure() -> None:
    """A failed claim commit must not leave an open transaction behind."""

    service, session, _, runs = create_service()
    runs.acquire_claim.return_value = Mock(spec=AssistantRunClaim)
    session.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(
            service.acquire_processing_claim(
                accepted_request=create_accepted_request(),
                lease_seconds=120,
                max_attempts=3,
            )
        )

    session.commit.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()


def test_fail_processing_marks_an_owned_claim_failed_and_commits() -> None:
    """A provider failure should durably release the worker's processing lease."""

    service, session, _, runs = create_service()
    claim = create_processing_claim()
    runs.fail_run.return_value = Mock()

    result = asyncio.run(
        service.fail_processing(
            claim=claim,
            error_code="model_timeout",
        )
    )

    assert result is None
    runs.fail_run.assert_awaited_once_with(
        run_id=claim.run.id,
        claim_token=claim.claim_token,
        error_code="model_timeout",
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_fail_processing_rolls_back_when_the_claim_is_no_longer_owned() -> None:
    """A stale worker must not overwrite a reclaimed run's state."""

    service, session, _, runs = create_service()
    runs.fail_run.return_value = None

    with pytest.raises(
        RuntimeError,
        match="Assistant processing claim is no longer owned",
    ):
        asyncio.run(
            service.fail_processing(
                claim=create_processing_claim(),
                error_code="provider_error",
            )
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


@pytest.mark.parametrize("acquired, claim_token", [(False, None), (True, None)])
def test_fail_processing_rejects_an_unowned_or_tokenless_claim(
    acquired: bool,
    claim_token,
) -> None:
    """Only an owned claim may update an assistant run's failure state."""

    service, session, _, runs = create_service()
    claim = create_processing_claim(acquired=acquired)
    claim.claim_token = claim_token

    expected_message = (
        "assistant processing claim was not acquired"
        if not acquired
        else "acquired assistant processing claim must have a token"
    )

    with pytest.raises(ValueError, match=expected_message):
        asyncio.run(
            service.fail_processing(
                claim=claim,
                error_code="provider_error",
            )
        )

    runs.fail_run.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_fail_processing_rolls_back_a_repository_failure() -> None:
    """Repository failures should leave the processing session reusable."""

    service, session, _, runs = create_service()
    runs.fail_run.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.fail_processing(
                claim=create_processing_claim(),
                error_code="provider_error",
            )
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_fail_processing_rolls_back_a_commit_failure() -> None:
    """A failed failure-state commit should be rolled back before propagating."""

    service, session, _, runs = create_service()
    runs.fail_run.return_value = Mock()
    session.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(
            service.fail_processing(
                claim=create_processing_claim(),
                error_code="provider_error",
            )
        )

    session.commit.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()
