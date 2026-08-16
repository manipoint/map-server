"""Authentication routes."""

from ipaddress import IPv4Address, IPv6Address, ip_address
from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import (
    AuthServiceDependency,
    ConnectionManagerDependency,
    CurrentPrincipal,
)
from app.auth.schemas import (
    AuthenticationResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    TokenPairResponse,
    UserResponse,
)
from app.auth.service import AuthenticationResult

router = APIRouter(prefix="/auth", tags=["authentication"])


def resolve_client_ip(request: Request) -> IPv4Address | IPv6Address | None:
    """Return the directly connected client's valid IP address."""

    if request.client is None:
        return None
    try:
        return ip_address(request.client.host)
    except ValueError:
        return None


def create_authentication_response(
    result: AuthenticationResult,
) -> AuthenticationResponse:
    """Convert an authentication result into its public API response."""
    return AuthenticationResponse(
        user=UserResponse.model_validate(result.user),
        tokens=TokenPairResponse(
            access_token=result.access_token.token,
            refresh_token=result.refresh_token.token,
            access_token_expires_at=result.access_token.expires_at,
            refresh_token_expires_at=result.refresh_token.expires_at,
        ),
    )


@router.post(
    "/register",
    response_model=AuthenticationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register_user(
    payload: RegisterRequest,
    request: Request,
    auth_service: AuthServiceDependency,
) -> AuthenticationResponse:
    """Register a user and create their first device session."""

    result = await auth_service.register(
        email=str(payload.email),
        password=payload.password.get_secret_value(),
        device_id=payload.device_id,
        device_name=payload.device_name,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    return create_authentication_response(result)


@router.post(
    "/login",
    response_model=AuthenticationResponse,
    summary="Authenticate a user",
)
async def login_user(
    payload: LoginRequest,
    request: Request,
    auth_service: AuthServiceDependency,
) -> AuthenticationResponse:
    """Authenticate credentials and create a device session."""
    result = await auth_service.login(
        email=str(payload.email),
        password=payload.password.get_secret_value(),
        device_id=payload.device_id,
        device_name=payload.device_name,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return create_authentication_response(result)


@router.post(
    "/refresh",
    response_model=AuthenticationResponse,
    summary="Rotate refresh credentials",
)
async def refresh_credentials(
    payload: RefreshRequest,
    request: Request,
    auth_service: AuthServiceDependency,
) -> AuthenticationResponse:
    """Rotate a refresh token and return new credentials."""

    result = await auth_service.refresh(
        refresh_token=payload.refresh_token.get_secret_value(),
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return create_authentication_response(result)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Log out the current device",
)
async def logout_current_device(
    principal: CurrentPrincipal,
    auth_service: AuthServiceDependency,
    connection_manager: ConnectionManagerDependency,
) -> Response:
    """Revoke the authentication session used by this request."""

    session_revoked = await auth_service.logout(
        user_id=principal.user.id, session_id=principal.auth_session.id
    )
    if session_revoked:
        await connection_manager.close_session(principal.auth_session.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Log out every device",
)
async def logout_all_devices(
    principal: CurrentPrincipal,
    auth_service: AuthServiceDependency,
    connection_manager: ConnectionManagerDependency,
) -> Response:
    """Revoke every active session belonging to the user."""

    await auth_service.logout_all(
        user_id=principal.user.id,
    )
    await connection_manager.close_user(principal.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/sessions",
    response_model=list[SessionResponse],
    summary="List active device sessions",
)
async def list_active_sessions(
    principal: CurrentPrincipal,
    auth_service: AuthServiceDependency,
) -> list[SessionResponse]:
    """Return all active sessions belonging to the authenticated user."""

    sessions = await auth_service.list_active_sessions(
        user_id=principal.user.id,
    )
    return [
        SessionResponse.model_validate(session).model_copy(
            update={"is_current": session.id == principal.auth_session.id}
        )
        for session in sessions
    ]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Log out the selected device",
)
async def logout_selected_device(
    session_id: UUID,
    principal: CurrentPrincipal,
    auth_service: AuthServiceDependency,
    connection_manager: ConnectionManagerDependency,
) -> Response:
    """Revoke one session belonging to the authenticated user."""

    session_revoked = await auth_service.logout(
        user_id=principal.user.id, session_id=session_id
    )
    if session_revoked:
        await connection_manager.close_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
