"""Provide persistence operations for travel conversation messages."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation
from app.database.models.message import Message


class MessageRepository:
    """Manage user and assistant messages in travel conversations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user_message_by_client_id(
        self,
        *,
        user_id: UUID,
        client_message_id: UUID,
        for_update: bool = False,
    ) -> Message | None:
        """Return a user-owned message by its client idempotency ID."""

        statement = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == user_id,
                Message.client_message_id == client_message_id,
                Message.role == "user",
            )
        )
        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_assistant_reply(
        self,
        *,
        user_id: UUID,
        reply_to_message_id: UUID,
    ) -> Message | None:
        """Return the latest assistant reply to a user-owned message."""

        statement = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == user_id,
                Message.reply_to_message_id == reply_to_message_id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_recent_by_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        limit: int,
    ) -> list[Message]:
        """Return recent user-owned conversation messages in chronological order."""

        if limit < 1:
            raise ValueError("limit must be greater than zero")

        statement = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Message.conversation_id == conversation_id,
                Conversation.user_id == user_id,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )

        result = await self.session.execute(statement)
        return list(reversed(result.scalars().all()))

    async def create_user_message(
        self,
        *,
        conversation_id: UUID,
        client_message_id: UUID,
        content: str,
    ) -> Message:
        """Create and flush a user message without committing."""
        message = Message(
            conversation_id=conversation_id,
            client_message_id=client_message_id,
            reply_to_message_id=None,
            role="user",
            content=content,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def create_assistant_message(
        self, *, conversation_id: UUID, reply_to_message_id: UUID, content: str
    ) -> Message:
        """Create and flush an assistant reply without committing."""
        message = Message(
            conversation_id=conversation_id,
            client_message_id=None,
            reply_to_message_id=reply_to_message_id,
            role="assistant",
            content=content,
        )

        self.session.add(message)
        await self.session.flush()
        return message
