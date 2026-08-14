"""Authentication application service."""

from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import (
    AccountNotActiveError,
    EmailAlreadyRegisteredError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
    SessionRevokedError,
)
from app.auth.passwords import (
    hash_password,
    perform_dummy_password_check,
    verify_password_and_update,
)
from app.auth.tokens import (
    AccessTokenClaims,
    IssuedAccessToken,
    IssuedRefreshToken,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_refresh_token,
)
from app.config import Settings
from app.database.models import User
from app.database.models.auth_session import AuthSession
from app.database.repositories.auth_sessions import AuthSessionRepository
from app.database.repositories.users import UserRepository


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """User, session, and credentials created by authentication."""

    user: User
    auth_session: AuthSession
    access_token: IssuedAccessToken
    refresh_token: IssuedRefreshToken


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Trusted user and session resolved from an access token."""

    user: User
    auth_session: AuthSession
    claims: AccessTokenClaims


class AuthService:
    """Coordinate authentication rules and database transactions."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        user_repository: UserRepository | None = None,
        auth_session_repository: AuthSessionRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.users = user_repository or UserRepository(session)
        self.auth_sessions = auth_session_repository or AuthSessionRepository(session)

    async def register(
        self,
        *,
        email: str,
        password: str,
        device_id: str,
        device_name: str | None,
        ip_address: IPv4Address | IPv6Address | None = None,
        user_agent: str | None = None,
    ) -> AuthenticationResult:
        """Register a user and create their first device session."""

        existing_user = await self.users.get_by_email(email=email)
        if existing_user is not None:
            raise EmailAlreadyRegisteredError(
                "An account with this email already exists"
            )
        password_hash = await hash_password(password=password)
        issued_at = datetime.now(UTC)
        try:
            try:
                user = await self.users.create(
                    email=email,
                    password_hash=password_hash,
                )
            except IntegrityError as error:
                raise EmailAlreadyRegisteredError(
                    "An account with this email already exists"
                ) from error

            refresh_token = create_refresh_token(self.settings, issued_at=issued_at)
            auth_session = await self.auth_sessions.create(
                user_id=user.id,
                refresh_token_hash=refresh_token.token_hash,
                device_id=device_id,
                device_name=device_name,
                expires_at=refresh_token.expires_at,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            access_token = create_access_token(
                user_id=user.id,
                session_id=auth_session.id,
                settings=self.settings,
                issued_at=issued_at,
            )
            await self.session.commit()

        except BaseException:
            await self.session.rollback()
            raise
        return AuthenticationResult(
            user=user,
            auth_session=auth_session,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    async def login(
        self,
        *,
        email: str,
        password: str,
        device_id: str,
        device_name: str | None,
        ip_address: IPv4Address | IPv6Address | None = None,
        user_agent: str | None = None,
    ) -> AuthenticationResult:
        """Authenticate credentials and create a new device session."""
        user = await self.users.get_by_email(email=email)
        if user is None:
            await perform_dummy_password_check(password)
            raise InvalidCredentialsError("Invalid email or password")
        password_valid, updated_password_hash = await verify_password_and_update(
            password=password,
            password_hash=user.password_hash,
        )
        if not password_valid:
            raise InvalidCredentialsError("Invalid email or password")
        if user.status != "active":
            raise AccountNotActiveError("User account is not active")
        issued_at = datetime.now(UTC)
        try:
            if updated_password_hash is not None:
                await self.users.update_password_hash(user, updated_password_hash)

            refresh_token = create_refresh_token(self.settings, issued_at=issued_at)

            auth_session = await self.auth_sessions.create(
                user_id=user.id,
                refresh_token_hash=refresh_token.token_hash,
                device_id=device_id,
                device_name=device_name,
                expires_at=refresh_token.expires_at,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            access_token = create_access_token(
                user_id=user.id,
                session_id=auth_session.id,
                settings=self.settings,
                issued_at=issued_at,
            )
            await self.session.commit()

        except BaseException:
            await self.session.rollback()
            raise
        return AuthenticationResult(
            user=user,
            auth_session=auth_session,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    async def refresh(
        self,
        *,
        refresh_token: str,
        ip_address: IPv4Address | IPv6Address | None = None,
        user_agent: str | None = None,
    ) -> AuthenticationResult:
        """Rotate a valid refresh token and issue new credentials."""

        issued_at = datetime.now(UTC)
        refresh_token_hash = hash_refresh_token(refresh_token, self.settings)

        security_error: Exception | None = None
        authentication_result: AuthenticationResult | None = None

        try:
            current_session = await self.auth_sessions.get_by_refresh_token_hash(
                refresh_token_hash, for_update=True
            )
            if current_session is None:
                raise InvalidRefreshTokenError("Invalid refresh token")
            if current_session.rotated_at is not None:
                await self.auth_sessions.revoke_token_family(
                    current_session.token_family_id,
                    revoked_at=issued_at,
                    reason="refresh_token_reuse",
                )
                await self.session.commit()

                security_error = RefreshTokenReuseError("Refresh token reuse detected")

            elif current_session.revoked_at is not None:
                raise SessionRevokedError("Authentication session has been revoked")
            elif current_session.expires_at <= issued_at:
                raise InvalidRefreshTokenError("Refresh token has expired")
            else:
                user = await self.users.get_by_id(current_session.user_id)
                if user is None or user.status != "active":
                    await self.auth_sessions.revoke_token_family(
                        current_session.token_family_id,
                        revoked_at=issued_at,
                        reason="account_not_active",
                    )
                    await self.session.commit()
                    security_error = AccountNotActiveError("User account is not active")
                else:
                    new_refresh_token = create_refresh_token(
                        self.settings, issued_at=issued_at
                    )
                    replacement_session = await self.auth_sessions.create(
                        user_id=user.id,
                        refresh_token_hash=new_refresh_token.token_hash,
                        device_id=current_session.device_id,
                        device_name=current_session.device_name,
                        expires_at=new_refresh_token.expires_at,
                        token_family_id=current_session.token_family_id,
                        ip_address=ip_address,
                        user_agent=user_agent,
                    )
                    rotation_succeeded = await self.auth_sessions.mark_rotated(
                        current_session,
                        replacement_session_id=replacement_session.id,
                        rotated_at=issued_at,
                    )

                    if not rotation_succeeded:
                        raise RuntimeError(
                            "Locked refresh session could not be rotated"
                        )

                    new_access_token = create_access_token(
                        user_id=user.id,
                        session_id=replacement_session.id,
                        settings=self.settings,
                        issued_at=issued_at,
                    )
                    await self.session.commit()
                    authentication_result = AuthenticationResult(
                        user=user,
                        auth_session=replacement_session,
                        access_token=new_access_token,
                        refresh_token=new_refresh_token,
                    )

        except BaseException:
            await self.session.rollback()
            raise
        if security_error is not None:
            raise security_error

        if authentication_result is None:
            raise RuntimeError("Refresh operation produced no authentication result")

        return authentication_result

    async def logout(self, *, user_id: UUID, session_id: UUID) -> bool:
        """Idempotently revoke one user-owned authentication session."""

        revoked_at = datetime.now(UTC)

        try:
            auth_session = await self.auth_sessions.get_by_id_for_user(
                session_id=session_id, user_id=user_id, for_update=True
            )
            session_revoked = False
            if auth_session is not None:
                session_revoked = await self.auth_sessions.revoke(
                    auth_session,
                    revoked_at=revoked_at,
                    reason="user_logout",
                )

            await self.session.commit()

        except BaseException:
            await self.session.rollback()
            raise
        return session_revoked

    async def logout_all(
        self,
        *,
        user_id: UUID,
    ) -> int:
        """Revoke every active authentication session for a user."""

        revoked_at = datetime.now(UTC)

        try:
            revoked_count = await self.auth_sessions.revoke_all_by_user(
                user_id,
                revoked_at=revoked_at,
                reason="user_logout_all",
            )
            await self.session.commit()

        except BaseException:
            await self.session.rollback()
            raise

        return revoked_count

    async def list_active_sessions(self, *, user_id: UUID) -> list[AuthSession]:
        """Return every active device session belonging to a user."""

        return await self.auth_sessions.list_active_by_user(
            user_id,
            current_time=datetime.now(UTC),
        )

    async def authenticate_access_token(
        self,
        *,
        access_token: str,
    ) -> AuthenticatedPrincipal:
        """Validate an access token and resolve its active user session."""

        claims = decode_access_token(
            access_token,
            self.settings,
        )
        current_time = datetime.now(UTC)

        auth_session = await self.auth_sessions.get_by_id_for_user(
            session_id=claims.session_id,
            user_id=claims.user_id,
        )

        if auth_session is None:
            raise InvalidAccessTokenError("Invalid access token")

        if (
            auth_session.revoked_at is not None
            or auth_session.rotated_at is not None
            or auth_session.expires_at <= current_time
        ):
            raise SessionRevokedError("Authentication session is no longer active")

        user = await self.users.get_by_id(claims.user_id)

        if user is None:
            raise InvalidAccessTokenError("Invalid access token")

        if user.status != "active":
            raise AccountNotActiveError("User account is not active")

        return AuthenticatedPrincipal(
            user=user,
            auth_session=auth_session,
            claims=claims,
        )
