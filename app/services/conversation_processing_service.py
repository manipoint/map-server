"""Prepare persisted conversation data for assistant processing."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.message import Message
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


class ConversationProcessingService:
    """Prepare conversation context while reusing completed responses."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        history_limit: int,
        message_repository: MessageRepository | None = None,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be greater than zero")
        self.session = session
        self.messages = message_repository or MessageRepository(session)
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
        content: str,
    ) -> SaveAssistantReply:
        """Persist one canonical assistant reply and commit the transaction."""
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("assistant reply content must not be blank")
        reply_to_message_id = accepted_request.user_message.id

        try:
            existing_reply = await self.messages.get_assistant_reply(
                user_id=user_id, reply_to_message_id=reply_to_message_id
            )
            if existing_reply is not None:
                await self.session.commit()
                return SaveAssistantReply(message=existing_reply, is_duplicate=True)
            assistant_message = await self.messages.create_assistant_message(
                conversation_id=accepted_request.conversation.id,
                reply_to_message_id=reply_to_message_id,
                content=normalized_content,
            )
            await self.session.commit()
            return SaveAssistantReply(message=assistant_message, is_duplicate=False)
        except IntegrityError as integrity_error:
            await self.session.rollback()
            try:
                existing_reply = await self.messages.get_assistant_reply(
                    user_id=user_id,
                    reply_to_message_id=reply_to_message_id,
                )
                if existing_reply is None:
                    raise integrity_error
                await self.session.commit()
                return SaveAssistantReply(
                    message=existing_reply,
                    is_duplicate=True,
                )

            except BaseException:
                await self.session.rollback()
                raise

        except BaseException:
            await self.session.rollback()
            raise
