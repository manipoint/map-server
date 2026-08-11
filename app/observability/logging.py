"""Structured logging configuration."""

import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime

_REDACTED_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "database_url",
        "jwt_signing_key",
        "password",
        "refresh_token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_password",
    "_secret",
    "_signing_key",
    "_token",
)
_STANDARD_LOG_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def _is_sensitive_key(key: str) -> bool:
    """Return whether a structured field name may contain a secret."""
    normalized_key = key.lower()

    return normalized_key in _REDACTED_KEYS or normalized_key.endswith(
        _SENSITIVE_SUFFIXES
    )


def _redact(value: object) -> object:
    """Recursively redact sensitive structured fields."""
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_name = str(key)
            if _is_sensitive_key(key_name):
                redacted[key_name] = "[REDACTED]"
            else:
                redacted[key_name] = _redact(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]

    return value


class JsonFormatter(logging.Formatter):
    """Format log records as one-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Convert a log record into JSON."""
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_FIELDS and not key.startswith("_"):
                if _is_sensitive_key(key):
                    payload[key] = "[REDACTED]"
                else:
                    payload[key] = _redact(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(log_level: str) -> None:
    """Configure root application logging."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())
    logging.captureWarnings(True)
