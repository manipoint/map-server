"""Authentication domain exceptions."""


class AuthenticationError(Exception):
    """Base exception for authentication failures."""


class EmailAlreadyRegisteredError(AuthenticationError):
    """Raised when registration uses an existing email address."""


class InvalidCredentialsError(AuthenticationError):
    """Raised when login credentials cannot be authenticated."""


class AccountNotActiveError(AuthenticationError):
    """Raised when an authenticated account is not active."""


class InvalidRefreshTokenError(AuthenticationError):
    """Raised when a refresh token cannot be trusted."""


class SessionRevokedError(AuthenticationError):
    """Raised when an authentication session has been revoked."""


class RefreshTokenReuseError(AuthenticationError):
    """Raised when a rotated refresh token is reused."""


class InvalidAccessTokenError(AuthenticationError):
    """Raised when an access token cannot be trusted."""
