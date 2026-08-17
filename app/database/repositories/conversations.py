"""Provide persistence operations for travel conversations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation import Conversation


class ConversationRepository:
    """Manage user-owned travel conversations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        locale: str,
        title: str | None = None,
    ) -> Conversation:
        """Create and flush a new conversation without committing."""

        conversation = Conversation(user_id=user_id, locale=locale, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get_by_id_for_user(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> Conversation | None:
        """Return a user-owned conversation, optionally locking its row."""

        statement = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
