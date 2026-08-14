"""Provide persistence operations for application users."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.user import User


class UserRepository:
    """Provide database operations for registered application users."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        """Return the user with the given normalized email address."""

        statement = select(User).where(User.email == email)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Return a user by primary key, or None when it does not exist."""

        return await self.session.get(User, user_id)

    async def create(self, *, email: str, password_hash: str) -> User:
        """Add and flush a new user without committing the transaction."""

        user = User(email=email, password_hash=password_hash)
        self.session.add(user)
        await self.session.flush()
        return user

    async def update_password_hash(self, user: User, password_hash: str) -> None:
        """Replace and flush a user's outdated password hash."""
        user.password_hash = password_hash
        await self.session.flush()
