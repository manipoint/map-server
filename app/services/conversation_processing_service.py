"""Prepare persisted conversation data for assistant processing."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.message import Message
from app.database.repositories.assistant_runs import (
    AssistantRunClaim,
    AssistantRunRepository,
)
from app.database.repositories.messages import MessageRepository
from app.services.conversation_service import AcceptedTravelRequest


@dataclass(frozen=True, slots=True)
class ConversationProcessingContext:
    """Persisted information required to process one travel request."""

    accepted_request: AcceptedTravelRequest
    history: tuple[Message, ...]
    cached_reply: Message | None

    @property
    def should_generate_reply(self) -> bool:
        """Return whether a new assistant response must be generated."""

        return self.cached_reply is None


@dataclass(frozen=True, slots=True)
class SaveAssistantReply:
    """The canonical assistant reply stored for a user message."""

    message: Message
    is_duplicate: bool


@dataclass(frozen=True, slots=True)
class ProcessingStart:
    """Decision made before invoking the travel model."""

    context: ConversationProcessingContext
    claim: AssistantRunClaim | None

    @property
    def should_invoke_model(self) -> bool:
        """Return whether this worker owns the model-processing lease."""
        return (
            self.context.cached_reply is None
            and self.claim is not None
            and self.claim.acquired
        )


class ConversationProcessingService:
    """Prepare conversation context while reusing completed responses."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        history_limit: int,
        message_repository: MessageRepository | None = None,
        assistant_run_repository: AssistantRunRepository | None = None,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be greater than zero")
        self.session = session
        self.messages = message_repository or MessageRepository(session)
        self.runs = assistant_run_repository or AssistantRunRepository(session)
        self.history_limit = history_limit

    async def prepare(
        self,
        *,
        user_id: UUID,
        accepted_request: AcceptedTravelRequest,
    ) -> ConversationProcessingContext:
        """Load an existing reply or bounded conversation history."""

        cached_reply = await self.messages.get_assistant_reply(
            user_id=user_id,
            reply_to_message_id=accepted_request.user_message.id,
        )
        if cached_reply is not None:
            return ConversationProcessingContext(
                accepted_request=accepted_request,
                history=(),
                cached_reply=cached_reply,
            )
        history = await self.messages.list_recent_by_conversation(
            conversation_id=accepted_request.conversation.id,
            user_id=user_id,
            limit=self.history_limit,
        )
        return ConversationProcessingContext(
            accepted_request=accepted_request,
            history=tuple(history),
            cached_reply=None,
        )

    async def save_reply(
        self,
        *,
        user_id: UUID,
        accepted_request: AcceptedTravelRequest,
        claim: AssistantRunClaim,
        content: str,
    ) -> SaveAssistantReply:
        """Persist a reply and complete its owned processing run atomically."""
        if not claim.acquired:
            raise ValueError("assistant processing claim was not acquired")
        if claim.claim_token is None:
            raise ValueError("acquired assistant processing claim must have a token")
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("assistant reply content must not be blank")
        reply_to_message_id = accepted_request.user_message.id

        try:
            existing_reply = await self.messages.get_assistant_reply(
                user_id=user_id, reply_to_message_id=reply_to_message_id
            )
            is_duplicate = existing_reply is not None
            if existing_reply is None:
                try:
                    assistant_message = await self.messages.create_assistant_message(
                        conversation_id=accepted_request.conversation.id,
                        reply_to_message_id=reply_to_message_id,
                        content=normalized_content,
                    )

                except IntegrityError:
                    await self.session.rollback()
                    assistant_message = await self.messages.get_assistant_reply(
                        user_id=user_id,
                        reply_to_message_id=reply_to_message_id,
                    )
                    if assistant_message is None:
                        raise
                    is_duplicate = True
            else:
                assistant_message = existing_reply
            completed_run = await self.runs.complete_run(
                run_id=claim.run.id,
                claim_token=claim.claim_token,
                assistant_message_id=assistant_message.id,
            )
            if completed_run is None:
                raise RuntimeError("Assistant processing claim is no longer owned")
            await self.session.commit()
            return SaveAssistantReply(
                message=assistant_message,
                is_duplicate=is_duplicate,
            )

        except BaseException:
            await self.session.rollback()
            raise

    async def acquire_processing_claim(
        self,
        *,
        accepted_request: AcceptedTravelRequest,
        lease_seconds: int,
        max_attempts: int,
    ) -> AssistantRunClaim:
        """Acquire and immediately commit an assistant processing lease."""
        try:
            claim = await self.runs.acquire_claim(
                user_message_id=accepted_request.user_message.id,
                lease_duration=timedelta(seconds=lease_seconds),
                max_attempts=max_attempts,
            )
            await self.session.commit()
            return claim
        except BaseException:
            await self.session.rollback()
            raise

    async def start_processing(
        self,
        *,
        user_id: UUID,
        accepted_request: AcceptedTravelRequest,
        lease_seconds: int,
        max_attempts: int,
    ) -> ProcessingStart:
        """Load context and decide whether this worker may invoke the model."""
        context = await self.prepare(
            user_id=user_id,
            accepted_request=accepted_request,
        )
        if context.cached_reply is not None:
            return ProcessingStart(
                context=context,
                claim=None,
            )
        claim = await self.acquire_processing_claim(
            accepted_request=accepted_request,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
        return ProcessingStart(context=context, claim=claim)

    async def fail_processing(
        self,
        *,
        claim: AssistantRunClaim,
        error_code: str,
    ) -> None:
        """Mark an owned assistant processing claim as failed."""

        if not claim.acquired:
            raise ValueError("assistant processing claim was not acquired")
        if claim.claim_token is None:
            raise ValueError("acquired assistant processing claim must have a token")

        try:
            failed_run = await self.runs.fail_run(
                run_id=claim.run.id,
                claim_token=claim.claim_token,
                error_code=error_code,
            )
            if failed_run is None:
                raise RuntimeError("Assistant processing claim is no longer owned")

            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
