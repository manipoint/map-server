"""Shared constants for the WebSocket protocol."""

from typing import Final

PROTOCOL_VERSION: Final[int] = 1

WS_IDLE_TIMEOUT_CODE: Final[int] = 1001
WS_UNSUPPORTED_DATA_CODE: Final[int] = 1003
WS_INVALID_PAYLOAD_CODE: Final[int] = 1007
WS_POLICY_VIOLATION_CODE: Final[int] = 1008
WS_MESSAGE_TOO_LARGE_CODE: Final[int] = 1009
WS_SERVICE_RESTART_CODE: Final[int] = 1012
WS_UNAUTHORIZED_CODE: Final[int] = 4401
WS_SESSION_REVOKED_CODE: Final[int] = 4401

WS_IDLE_TIMEOUT_REASON: Final[str] = "Connection idle timeout"
WS_UNSUPPORTED_DATA_REASON: Final[str] = "Text JSON messages are required"
WS_INVALID_PAYLOAD_REASON: Final[str] = "Invalid JSON payload"
WS_POLICY_VIOLATION_REASON: Final[str] = "Invalid client event"
WS_MESSAGE_TOO_LARGE_REASON: Final[str] = "Message exceeds maximum size"
WS_SERVICE_RESTART_REASON: Final[str] = "Service restarting"
WS_UNAUTHORIZED_REASON: Final[str] = "Unauthorized"
WS_SESSION_REVOKED_REASON: Final[str] = "Authentication session revoked"
