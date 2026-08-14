"""Authentication request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)


class DeviceDetails(BaseModel):
    """Client device information associated with a login session."""

    device_id: str = Field(min_length=16, max_length=255)
    device_name: str | None = Field(default=None, max_length=255)

    @field_validator("device_id")
    @classmethod
    def normalize_device_id(cls, value: str) -> str:
        """Reject surrounding whitespace in device identifiers."""

        normalized = value.strip()
        if normalized != value:
            raise ValueError("device_id must not contain surrounding whitespace")
        return normalized

    @field_validator("device_name")
    @classmethod
    def normalize_device_name(cls, value: str | None) -> str | None:
        """Trim optional human-readable device names."""

        if value is None:
            return None

        normalized = value.strip()
        return normalized or None


class RegisterRequest(DeviceDetails):
    """Data required to register a user and create the first session."""

    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        """Normalize string emails before validation and storage."""

        if isinstance(value, str):
            return value.strip().lower()
        return value


class LoginRequest(DeviceDetails):
    """Credentials and device details required for login."""

    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        """Normalize string emails before account lookup."""

        if isinstance(value, str):
            return value.strip().lower()
        return value


class RefreshRequest(BaseModel):
    """Refresh credential submitted to rotate a session."""

    refresh_token: SecretStr = Field(min_length=32, max_length=2048)


class TokenPairResponse(BaseModel):
    """New credentials returned after login or refresh."""

    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    token_type: str = "bearer"
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime


class UserResponse(BaseModel):
    """Public user information returned by the authentication API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    status: str
    created_at: datetime


class SessionResponse(BaseModel):
    """Safe device-session information without credential hashes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_id: str
    device_name: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
