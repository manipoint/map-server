"""Tests for the authentication application service."""

import asyncio
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.auth.service as service_module
from app.auth.exceptions import (
    AccountNotActiveError,
    EmailAlreadyRegisteredError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
    SessionRevokedError,
)
from app.auth.service import AuthService
from app.auth.tokens import create_access_token, hash_refresh_token
from app.config import Settings
from app.database.models.auth_session import AuthSession
from app.database.models.user import User
from app.database.repositories.auth_sessions import AuthSessionRepository
from app.database.repositories.users import UserRepository


def create_settings() -> Settings:
    """Create isolated settings for authentication-service tests."""

    return Settings(
        _env_file=None,
        database_connection_mode="url",
        database_url=SecretStr(
            "postgresql+asyncpg://travel:test@localhost/travel_test"
        ),
        jwt_signing_key=SecretStr("test-signing-key-at-least-32-characters"),
        refresh_token_hash_key=SecretStr(
            "test-refresh-hash-key-at-least-32-characters"
        ),
    )


def create_dependencies() -> tuple[Mock, Mock, Mock]:
    """Create mocked database and repository dependencies."""

    session = Mock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    users = Mock(spec=UserRepository)
    users.get_by_email = AsyncMock()
    users.get_by_id = AsyncMock()
    users.create = AsyncMock()
    users.update_password_hash = AsyncMock()

    auth_sessions = Mock(spec=AuthSessionRepository)
    auth_sessions.create = AsyncMock()
    auth_sessions.get_by_id_for_user = AsyncMock()
    auth_sessions.get_by_refresh_token_hash = AsyncMock()
    auth_sessions.list_active_by_user = AsyncMock()
    auth_sessions.mark_rotated = AsyncMock()
    auth_sessions.revoke = AsyncMock()
    auth_sessions.revoke_all_by_user = AsyncMock()
    auth_sessions.revoke_token_family = AsyncMock()
    return session, users, auth_sessions


def test_register_creates_user_session_and_credentials(monkeypatch) -> None:
    """Registration should atomically create an authenticated session."""

    user_id = uuid4()
    auth_session_id = uuid4()
    user = User(
        id=user_id,
        email="user@example.com",
        password_hash="stored-password-hash",
    )
    auth_session = AuthSession(
        id=auth_session_id,
        user_id=user_id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    async def fake_hash_password(password: str) -> str:
        assert password == "correct horse battery staple"
        return "stored-password-hash"

    monkeypatch.setattr(service_module, "hash_password", fake_hash_password)

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = None
    users.create.return_value = user
    auth_sessions.create.return_value = auth_session

    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    result = asyncio.run(
        service.register(
            email="user@example.com",
            password="correct horse battery staple",
            device_id="flutter-device-123456",
            device_name="Imran's iPhone",
            user_agent="TravelAssistant/1.0",
        )
    )

    assert result.user is user
    assert result.auth_session is auth_session
    assert result.access_token.token
    assert result.refresh_token.token
    assert result.refresh_token.token_hash

    users.get_by_email.assert_awaited_once_with(email="user@example.com")
    users.create.assert_awaited_once_with(
        email="user@example.com",
        password_hash="stored-password-hash",
    )
    auth_sessions.create.assert_awaited_once()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_register_rejects_existing_email() -> None:
    """An existing email should be rejected before performing writes."""

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = Mock(spec=User)

    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(
        EmailAlreadyRegisteredError,
        match="already exists",
    ):
        asyncio.run(
            service.register(
                email="user@example.com",
                password="correct horse battery staple",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    users.create.assert_not_awaited()
    auth_sessions.create.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_register_converts_integrity_error_and_rolls_back(monkeypatch) -> None:
    """A concurrent duplicate insert should roll back and use a domain error."""

    async def fake_hash_password(password: str) -> str:
        return "stored-password-hash"

    monkeypatch.setattr(service_module, "hash_password", fake_hash_password)

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = None
    users.create.side_effect = IntegrityError(
        "INSERT INTO users",
        {},
        Exception("duplicate email"),
    )

    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(EmailAlreadyRegisteredError, match="already exists"):
        asyncio.run(
            service.register(
                email="user@example.com",
                password="correct horse battery staple",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    auth_sessions.create.assert_not_awaited()


def test_register_rolls_back_unexpected_session_failure(monkeypatch) -> None:
    """A session-creation failure should roll back the new user."""

    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
    )

    async def fake_hash_password(password: str) -> str:
        return "stored-password-hash"

    monkeypatch.setattr(service_module, "hash_password", fake_hash_password)

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = None
    users.create.return_value = user
    auth_sessions.create.side_effect = RuntimeError("session creation failed")

    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(RuntimeError, match="session creation failed"):
        asyncio.run(
            service.register(
                email="user@example.com",
                password="correct horse battery staple",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_register_does_not_misclassify_session_integrity_error(monkeypatch) -> None:
    """A session constraint failure should not look like a duplicate email."""

    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
    )
    session_error = IntegrityError(
        "INSERT INTO auth_sessions",
        {},
        Exception("session constraint failed"),
    )

    async def fake_hash_password(password: str) -> str:
        return "stored-password-hash"

    monkeypatch.setattr(service_module, "hash_password", fake_hash_password)

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = None
    users.create.return_value = user
    auth_sessions.create.side_effect = session_error

    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(IntegrityError) as error_info:
        asyncio.run(
            service.register(
                email="user@example.com",
                password="correct horse battery staple",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    assert error_info.value is session_error
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_login_creates_session_and_credentials(monkeypatch) -> None:
    """Valid credentials should create and return a device session."""

    user_id = uuid4()
    auth_session_id = uuid4()
    user = User(
        id=user_id,
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    created_auth_session = AuthSession(
        id=auth_session_id,
        user_id=user_id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    verify_password = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(
        service_module,
        "verify_password_and_update",
        verify_password,
    )

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = user
    auth_sessions.create.return_value = created_auth_session
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    result = asyncio.run(
        service.login(
            email="user@example.com",
            password="correct horse battery staple",
            device_id="flutter-device-123456",
            device_name="Imran's iPhone",
            user_agent="TravelAssistant/1.0",
        )
    )

    assert result.user is user
    assert result.auth_session is created_auth_session
    assert result.access_token.token
    assert result.refresh_token.token
    verify_password.assert_awaited_once_with(
        password="correct horse battery staple",
        password_hash="stored-password-hash",
    )
    users.update_password_hash.assert_not_awaited()
    auth_sessions.create.assert_awaited_once()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_login_unknown_email_performs_dummy_password_check(monkeypatch) -> None:
    """An unknown email should use timing protection and generic failure."""

    dummy_password_check = AsyncMock()
    verify_password = AsyncMock()
    monkeypatch.setattr(
        service_module,
        "perform_dummy_password_check",
        dummy_password_check,
    )
    monkeypatch.setattr(
        service_module,
        "verify_password_and_update",
        verify_password,
    )

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = None
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(InvalidCredentialsError, match="Invalid email or password"):
        asyncio.run(
            service.login(
                email="missing@example.com",
                password="unknown password",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    dummy_password_check.assert_awaited_once_with("unknown password")
    verify_password.assert_not_awaited()
    auth_sessions.create.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_login_rejects_wrong_password(monkeypatch) -> None:
    """A wrong password should return the generic credential failure."""

    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    verify_password = AsyncMock(return_value=(False, None))
    monkeypatch.setattr(
        service_module,
        "verify_password_and_update",
        verify_password,
    )

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = user
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(InvalidCredentialsError, match="Invalid email or password"):
        asyncio.run(
            service.login(
                email="user@example.com",
                password="wrong password",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    users.update_password_hash.assert_not_awaited()
    auth_sessions.create.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_login_rejects_inactive_account(monkeypatch) -> None:
    """Valid credentials should not create a session for an inactive user."""

    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="disabled",
    )
    monkeypatch.setattr(
        service_module,
        "verify_password_and_update",
        AsyncMock(return_value=(True, None)),
    )

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = user
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(AccountNotActiveError, match="not active"):
        asyncio.run(
            service.login(
                email="user@example.com",
                password="correct horse battery staple",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    auth_sessions.create.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_login_updates_outdated_password_hash(monkeypatch) -> None:
    """A valid old password hash should be upgraded in the login transaction."""

    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="old-password-hash",
        status="active",
    )
    created_auth_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    monkeypatch.setattr(
        service_module,
        "verify_password_and_update",
        AsyncMock(return_value=(True, "new-password-hash")),
    )

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = user
    auth_sessions.create.return_value = created_auth_session
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    asyncio.run(
        service.login(
            email="user@example.com",
            password="correct horse battery staple",
            device_id="flutter-device-123456",
            device_name=None,
        )
    )

    users.update_password_hash.assert_awaited_once_with(
        user,
        "new-password-hash",
    )
    session.commit.assert_awaited_once_with()


def test_login_rolls_back_session_creation_failure(monkeypatch) -> None:
    """A failed device-session insert should roll back the login transaction."""

    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    monkeypatch.setattr(
        service_module,
        "verify_password_and_update",
        AsyncMock(return_value=(True, None)),
    )

    session, users, auth_sessions = create_dependencies()
    users.get_by_email.return_value = user
    auth_sessions.create.side_effect = RuntimeError("session creation failed")
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(RuntimeError, match="session creation failed"):
        asyncio.run(
            service.login(
                email="user@example.com",
                password="correct horse battery staple",
                device_id="flutter-device-123456",
                device_name=None,
            )
        )

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_refresh_rotates_session_and_returns_new_credentials() -> None:
    """A valid refresh token should rotate its locked device session."""

    settings = create_settings()
    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    token_family_id = uuid4()
    current_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        refresh_token_hash="old-refresh-token-hash",
        token_family_id=token_family_id,
        device_id="flutter-device-123456",
        device_name="Imran's iPhone",
        expires_at=datetime.now(UTC) + timedelta(days=10),
        ip_address=ip_address("198.51.100.10"),
        user_agent="TravelAssistant/old",
    )
    replacement_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        refresh_token_hash="new-refresh-token-hash",
        token_family_id=token_family_id,
        device_id=current_session.device_id,
        device_name=current_session.device_name,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    new_ip_address = ip_address("203.0.113.20")

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_refresh_token_hash.return_value = current_session
    auth_sessions.create.return_value = replacement_session
    auth_sessions.mark_rotated.return_value = True
    users.get_by_id.return_value = user
    service = AuthService(
        session=session,
        settings=settings,
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    result = asyncio.run(
        service.refresh(
            refresh_token="raw-refresh-token",
            ip_address=new_ip_address,
            user_agent="TravelAssistant/new",
        )
    )

    expected_hash = hash_refresh_token("raw-refresh-token", settings)
    auth_sessions.get_by_refresh_token_hash.assert_awaited_once_with(
        expected_hash,
        for_update=True,
    )
    users.get_by_id.assert_awaited_once_with(user.id)

    create_arguments = auth_sessions.create.await_args.kwargs
    assert create_arguments["user_id"] == user.id
    assert create_arguments["token_family_id"] == token_family_id
    assert create_arguments["device_id"] == current_session.device_id
    assert create_arguments["device_name"] == current_session.device_name
    assert create_arguments["ip_address"] == new_ip_address
    assert create_arguments["user_agent"] == "TravelAssistant/new"

    auth_sessions.mark_rotated.assert_awaited_once_with(
        current_session,
        replacement_session_id=replacement_session.id,
        rotated_at=create_arguments["expires_at"] - timedelta(days=30),
    )
    assert result.user is user
    assert result.auth_session is replacement_session
    assert result.access_token.token
    assert result.refresh_token.token
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_refresh_rejects_unknown_token() -> None:
    """An unknown refresh token should fail after a locked lookup."""

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_refresh_token_hash.return_value = None
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(InvalidRefreshTokenError, match="Invalid refresh token"):
        asyncio.run(service.refresh(refresh_token="unknown-refresh-token"))

    auth_sessions.create.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_refresh_rejects_revoked_session() -> None:
    """A manually revoked session should not issue new credentials."""

    current_session = Mock(spec=AuthSession)
    current_session.rotated_at = None
    current_session.revoked_at = datetime.now(UTC) - timedelta(minutes=1)

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_refresh_token_hash.return_value = current_session
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(SessionRevokedError, match="revoked"):
        asyncio.run(service.refresh(refresh_token="revoked-refresh-token"))

    users.get_by_id.assert_not_awaited()
    auth_sessions.create.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_refresh_rejects_expired_session() -> None:
    """An expired refresh session should not issue new credentials."""

    current_session = Mock(spec=AuthSession)
    current_session.rotated_at = None
    current_session.revoked_at = None
    current_session.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_refresh_token_hash.return_value = current_session
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(InvalidRefreshTokenError, match="expired"):
        asyncio.run(service.refresh(refresh_token="expired-refresh-token"))

    users.get_by_id.assert_not_awaited()
    auth_sessions.create.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_refresh_reuse_revokes_family_before_raising() -> None:
    """Reuse of a rotated token should commit family revocation first."""

    token_family_id = uuid4()
    current_session = Mock(spec=AuthSession)
    current_session.rotated_at = datetime.now(UTC) - timedelta(minutes=1)
    current_session.token_family_id = token_family_id

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_refresh_token_hash.return_value = current_session
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(RefreshTokenReuseError, match="reuse detected"):
        asyncio.run(service.refresh(refresh_token="reused-refresh-token"))

    auth_sessions.revoke_token_family.assert_awaited_once()
    revoke_arguments = auth_sessions.revoke_token_family.await_args
    assert revoke_arguments.args == (token_family_id,)
    assert revoke_arguments.kwargs["reason"] == "refresh_token_reuse"
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    auth_sessions.create.assert_not_awaited()


@pytest.mark.parametrize("user_status", [None, "disabled"])
def test_refresh_revokes_family_for_inactive_account(
    user_status: str | None,
) -> None:
    """A missing or inactive user should revoke the refresh-token family."""

    user_id = uuid4()
    token_family_id = uuid4()
    current_session = Mock(spec=AuthSession)
    current_session.rotated_at = None
    current_session.revoked_at = None
    current_session.expires_at = datetime.now(UTC) + timedelta(days=1)
    current_session.user_id = user_id
    current_session.token_family_id = token_family_id

    user = None
    if user_status is not None:
        user = User(
            id=user_id,
            email="user@example.com",
            password_hash="stored-password-hash",
            status=user_status,
        )

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_refresh_token_hash.return_value = current_session
    users.get_by_id.return_value = user
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(AccountNotActiveError, match="not active"):
        asyncio.run(service.refresh(refresh_token="account-refresh-token"))

    auth_sessions.revoke_token_family.assert_awaited_once()
    revoke_arguments = auth_sessions.revoke_token_family.await_args
    assert revoke_arguments.args == (token_family_id,)
    assert revoke_arguments.kwargs["reason"] == "account_not_active"
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_refresh_rolls_back_when_rotation_cannot_complete() -> None:
    """A failed rotation should roll back its replacement session."""

    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    current_session = Mock(spec=AuthSession)
    current_session.rotated_at = None
    current_session.revoked_at = None
    current_session.expires_at = datetime.now(UTC) + timedelta(days=1)
    current_session.user_id = user.id
    current_session.token_family_id = uuid4()
    current_session.device_id = "flutter-device-123456"
    current_session.device_name = None
    current_session.ip_address = None
    current_session.user_agent = None
    replacement_session = Mock(spec=AuthSession)
    replacement_session.id = uuid4()

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_refresh_token_hash.return_value = current_session
    auth_sessions.create.return_value = replacement_session
    auth_sessions.mark_rotated.return_value = False
    users.get_by_id.return_value = user
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(RuntimeError, match="could not be rotated"):
        asyncio.run(service.refresh(refresh_token="valid-refresh-token"))

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_logout_revokes_owned_session() -> None:
    """Logout should lock and revoke the authenticated user's session."""

    user_id = uuid4()
    auth_session_id = uuid4()
    owned_session = Mock(spec=AuthSession)

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = owned_session
    auth_sessions.revoke.return_value = True
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    session_revoked = asyncio.run(
        service.logout(
            user_id=user_id,
            session_id=auth_session_id,
        )
    )

    assert session_revoked is True
    auth_sessions.get_by_id_for_user.assert_awaited_once_with(
        session_id=auth_session_id,
        user_id=user_id,
        for_update=True,
    )
    auth_sessions.revoke.assert_awaited_once()
    revoke_arguments = auth_sessions.revoke.await_args
    assert revoke_arguments.args == (owned_session,)
    assert revoke_arguments.kwargs["reason"] == "user_logout"
    assert revoke_arguments.kwargs["revoked_at"].tzinfo is UTC
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_logout_is_idempotent_when_session_is_missing() -> None:
    """A missing or unowned session should produce a successful no-op."""

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = None
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    session_revoked = asyncio.run(
        service.logout(
            user_id=uuid4(),
            session_id=uuid4(),
        )
    )

    assert session_revoked is False
    auth_sessions.revoke.assert_not_awaited()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_logout_is_idempotent_when_session_is_already_revoked() -> None:
    """Repeated logout should preserve the successful no-op behavior."""

    owned_session = Mock(spec=AuthSession)
    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = owned_session
    auth_sessions.revoke.return_value = False
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    session_revoked = asyncio.run(
        service.logout(
            user_id=uuid4(),
            session_id=uuid4(),
        )
    )

    assert session_revoked is False
    auth_sessions.revoke.assert_awaited_once()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_logout_rolls_back_revocation_failure() -> None:
    """A failed revocation should roll back the logout transaction."""

    owned_session = Mock(spec=AuthSession)
    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = owned_session
    auth_sessions.revoke.side_effect = RuntimeError("revocation failed")
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(RuntimeError, match="revocation failed"):
        asyncio.run(
            service.logout(
                user_id=uuid4(),
                session_id=uuid4(),
            )
        )

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_logout_all_revokes_every_active_session() -> None:
    """Logout-all should commit revocation of every active user session."""

    user_id = uuid4()
    session, users, auth_sessions = create_dependencies()
    auth_sessions.revoke_all_by_user.return_value = 3
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    revoked_count = asyncio.run(service.logout_all(user_id=user_id))

    assert revoked_count == 3
    auth_sessions.revoke_all_by_user.assert_awaited_once()
    revoke_arguments = auth_sessions.revoke_all_by_user.await_args
    assert revoke_arguments.args == (user_id,)
    assert revoke_arguments.kwargs["reason"] == "user_logout_all"
    assert revoke_arguments.kwargs["revoked_at"].tzinfo is UTC
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_logout_all_is_idempotent_without_active_sessions() -> None:
    """Logout-all should commit successfully when nothing needs revocation."""

    session, users, auth_sessions = create_dependencies()
    auth_sessions.revoke_all_by_user.return_value = 0
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    revoked_count = asyncio.run(service.logout_all(user_id=uuid4()))

    assert revoked_count == 0
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_logout_all_rolls_back_repository_failure() -> None:
    """A bulk-revocation failure should roll back logout-all."""

    session, users, auth_sessions = create_dependencies()
    auth_sessions.revoke_all_by_user.side_effect = RuntimeError(
        "bulk revocation failed"
    )
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(RuntimeError, match="bulk revocation failed"):
        asyncio.run(service.logout_all(user_id=uuid4()))

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_list_active_sessions_returns_user_sessions() -> None:
    """Active-session listing should return the repository result unchanged."""

    user_id = uuid4()
    first_session = Mock(spec=AuthSession)
    second_session = Mock(spec=AuthSession)

    session, users, auth_sessions = create_dependencies()
    auth_sessions.list_active_by_user.return_value = [
        first_session,
        second_session,
    ]
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )
    time_before_call = datetime.now(UTC)

    result = asyncio.run(service.list_active_sessions(user_id=user_id))

    time_after_call = datetime.now(UTC)
    assert result == [first_session, second_session]
    auth_sessions.list_active_by_user.assert_awaited_once()
    list_arguments = auth_sessions.list_active_by_user.await_args
    assert list_arguments.args == (user_id,)
    assert time_before_call <= list_arguments.kwargs["current_time"] <= time_after_call
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_list_active_sessions_returns_empty_list() -> None:
    """A user without active devices should receive an empty list."""

    session, users, auth_sessions = create_dependencies()
    auth_sessions.list_active_by_user.return_value = []
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    result = asyncio.run(service.list_active_sessions(user_id=uuid4()))

    assert result == []
    session.commit.assert_not_awaited()


def test_list_active_sessions_propagates_repository_failure() -> None:
    """A session-listing database failure should propagate to the caller."""

    session, users, auth_sessions = create_dependencies()
    auth_sessions.list_active_by_user.side_effect = RuntimeError(
        "session listing failed"
    )
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(RuntimeError, match="session listing failed"):
        asyncio.run(service.list_active_sessions(user_id=uuid4()))

    session.commit.assert_not_awaited()


def test_authenticate_access_token_returns_trusted_principal() -> None:
    """A valid token should resolve its active user-owned session."""

    settings = create_settings()
    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="active",
    )
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    access_token = create_access_token(
        user_id=user.id,
        session_id=auth_session.id,
        settings=settings,
    )

    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = auth_session
    users.get_by_id.return_value = user
    service = AuthService(
        session=session,
        settings=settings,
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    principal = asyncio.run(
        service.authenticate_access_token(
            access_token=access_token.token,
        )
    )

    assert principal.user is user
    assert principal.auth_session is auth_session
    assert principal.claims.user_id == user.id
    assert principal.claims.session_id == auth_session.id
    auth_sessions.get_by_id_for_user.assert_awaited_once_with(
        session_id=auth_session.id,
        user_id=user.id,
    )
    users.get_by_id.assert_awaited_once_with(user.id)
    session.commit.assert_not_awaited()


def test_authenticate_access_token_rejects_malformed_token() -> None:
    """A malformed token should fail before database access."""

    session, users, auth_sessions = create_dependencies()
    service = AuthService(
        session=session,
        settings=create_settings(),
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(InvalidAccessTokenError, match="Invalid access token"):
        asyncio.run(
            service.authenticate_access_token(
                access_token="not-a-valid-jwt",
            )
        )

    auth_sessions.get_by_id_for_user.assert_not_awaited()
    users.get_by_id.assert_not_awaited()


def test_authenticate_access_token_rejects_missing_session() -> None:
    """A signed token without its server session should not be trusted."""

    settings = create_settings()
    access_token = create_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
    )
    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = None
    service = AuthService(
        session=session,
        settings=settings,
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(InvalidAccessTokenError, match="Invalid access token"):
        asyncio.run(
            service.authenticate_access_token(
                access_token=access_token.token,
            )
        )

    users.get_by_id.assert_not_awaited()


@pytest.mark.parametrize(
    "session_state",
    ["revoked", "rotated", "expired"],
)
def test_authenticate_access_token_rejects_inactive_session(
    session_state: str,
) -> None:
    """A revoked, rotated, or expired server session should be rejected."""

    settings = create_settings()
    user_id = uuid4()
    auth_session_id = uuid4()
    current_time = datetime.now(UTC)
    auth_session = AuthSession(
        id=auth_session_id,
        user_id=user_id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=current_time + timedelta(days=30),
    )

    if session_state == "revoked":
        auth_session.revoked_at = current_time
    elif session_state == "rotated":
        auth_session.rotated_at = current_time
    else:
        auth_session.expires_at = current_time - timedelta(minutes=1)

    access_token = create_access_token(
        user_id=user_id,
        session_id=auth_session_id,
        settings=settings,
    )
    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = auth_session
    service = AuthService(
        session=session,
        settings=settings,
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(SessionRevokedError, match="no longer active"):
        asyncio.run(
            service.authenticate_access_token(
                access_token=access_token.token,
            )
        )

    users.get_by_id.assert_not_awaited()


def test_authenticate_access_token_rejects_missing_user() -> None:
    """A session whose user no longer exists should not authenticate."""

    settings = create_settings()
    user_id = uuid4()
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user_id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    access_token = create_access_token(
        user_id=user_id,
        session_id=auth_session.id,
        settings=settings,
    )
    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = auth_session
    users.get_by_id.return_value = None
    service = AuthService(
        session=session,
        settings=settings,
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(InvalidAccessTokenError, match="Invalid access token"):
        asyncio.run(
            service.authenticate_access_token(
                access_token=access_token.token,
            )
        )


def test_authenticate_access_token_rejects_inactive_user() -> None:
    """An inactive account should not authenticate with an old access token."""

    settings = create_settings()
    user = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="stored-password-hash",
        status="disabled",
    )
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        refresh_token_hash="stored-refresh-token-hash",
        token_family_id=uuid4(),
        device_id="flutter-device-123456",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    access_token = create_access_token(
        user_id=user.id,
        session_id=auth_session.id,
        settings=settings,
    )
    session, users, auth_sessions = create_dependencies()
    auth_sessions.get_by_id_for_user.return_value = auth_session
    users.get_by_id.return_value = user
    service = AuthService(
        session=session,
        settings=settings,
        user_repository=users,
        auth_session_repository=auth_sessions,
    )

    with pytest.raises(AccountNotActiveError, match="not active"):
        asyncio.run(
            service.authenticate_access_token(
                access_token=access_token.token,
            )
        )
