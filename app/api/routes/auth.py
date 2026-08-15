"""Authentication routes."""

from ipaddress import IPv4Address, IPv6Address, ip_address

from fastapi import APIRouter, Request, status

from app.api.dependencies import AuthServiceDependency
from app.auth.schemas import (
    AuthenticationResponse,
    LoginRequest,
    RegisterRequest,
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
