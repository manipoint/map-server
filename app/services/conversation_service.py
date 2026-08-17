"""Conversation persistence use cases."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation
from app.database.models.message import Message
from app.database.repositories.conversations import ConversationRepository
from app.database.repositories.messages import MessageRepository
from app.domain.errors import ClientMessageConflictError, ConversationNotFoundError


@dataclass(frozen=True, slots=True)
class AcceptedTravelRequest:
    """A durable user request accepted for later processing."""

    conversation: Conversation
    user_message: Message
    is_duplicate: bool


def derive_conversation_title(message: str) -> str:
    """Derive a short title without spending an LLM request."""
    normalized_message = " ".join(message.split())
    return normalized_message[:160]


class ConversationService:
    """Coordinate conversation persistence and transaction boundaries."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        conversation_repository: ConversationRepository | None = None,
        message_repository: MessageRepository | None = None,
    ) -> None:
        self.session = session
        self.conversations = conversation_repository or ConversationRepository(session)
        self.messages = message_repository or MessageRepository(session)

    async def accept_request(
        self,
        *,
        user_id: UUID,
        client_message_id: UUID,
        conversation_id: UUID | None,
        message: str,
        locale: str,
    ) -> AcceptedTravelRequest:
        """Persist a valid request exactly once and commit it."""

        try:
            existing_message = await self.messages.get_user_message_by_client_id(
                user_id=user_id,
                client_message_id=client_message_id,
            )
            if existing_message is not None:
                result = await self._build_duplicate_result(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message=message,
                    existing_message=existing_message,
                )
                await self.session.commit()
                return result

            if conversation_id is None:
                conversation = await self.conversations.create(
                    user_id=user_id,
                    locale=locale,
                    title=derive_conversation_title(message),
                )
            else:
                conversation = await self.conversations.get_by_id_for_user(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    for_update=True,
                )
                if conversation is None:
                    raise ConversationNotFoundError("Conversation was not found")
                conversation.locale = locale
                conversation.updated_at = datetime.now(UTC)
            user_message = await self.messages.create_user_message(
                conversation_id=conversation.id,
                client_message_id=client_message_id,
                content=message,
            )
            await self.session.commit()
        except IntegrityError as integrity_error:
            await self.session.rollback()
            try:
                existing_message = await self.messages.get_user_message_by_client_id(
                    user_id=user_id,
                    client_message_id=client_message_id,
                )
                if existing_message is None:
                    raise integrity_error

                result = await self._build_duplicate_result(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message=message,
                    existing_message=existing_message,
                )
                await self.session.commit()
                return result
            except BaseException:
                await self.session.rollback()
                raise
        except BaseException:
            await self.session.rollback()
            raise

        return AcceptedTravelRequest(
            conversation=conversation,
            user_message=user_message,
            is_duplicate=False,
        )

    async def _build_duplicate_result(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID | None,
        message: str,
        existing_message: Message,
    ) -> AcceptedTravelRequest:
        """Validate and return an earlier idempotent request."""
        if existing_message.content != message:
            raise ClientMessageConflictError(
                "Client message ID was reused with different content"
            )
        if (
            conversation_id is not None
            and existing_message.conversation_id != conversation_id
        ):
            raise ClientMessageConflictError(
                "Client message ID belongs to another conversation"
            )

        conversation = await self.conversations.get_by_id_for_user(
            conversation_id=existing_message.conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise RuntimeError("Stored message references an unavailable conversation")

        return AcceptedTravelRequest(
            conversation=conversation,
            user_message=existing_message,
            is_duplicate=True,
        )
