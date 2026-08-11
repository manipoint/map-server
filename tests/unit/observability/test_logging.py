"""Tests for structured logging."""

import json
import logging

from app.observability.logging import JsonFormatter, configure_logging


def make_log_record() -> logging.LogRecord:
    """Create a basic log record for formatter tests."""

    return logging.LogRecord(
        name="travel.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Search completed",
        args=(),
        exc_info=None,
    )


def test_json_formatter_outputs_structured_log() -> None:
    """The formatter should produce valid structured JSON."""

    record = make_log_record()
    record.request_id = "request-123"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "travel.test"
    assert payload["message"] == "Search completed"
    assert payload["request_id"] == "request-123"
    assert "timestamp" in payload


def test_json_formatter_redacts_sensitive_fields() -> None:
    """Sensitive structured values should be redacted."""

    record = make_log_record()
    record.metadata = {
        "api_key": "secret-value",
        "city": "Lahore",
        "openai_api_key": "provider-secret",
    }
    record.groq_api_key = "top-level-provider-secret"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["metadata"]["api_key"] == "[REDACTED]"
    assert payload["metadata"]["openai_api_key"] == "[REDACTED]"
    assert payload["metadata"]["city"] == "Lahore"
    assert payload["groq_api_key"] == "[REDACTED]"


def test_configure_logging_does_not_duplicate_handlers(monkeypatch) -> None:
    """Repeated configuration should keep a single root handler."""

    test_logger = logging.Logger("logging-configuration-test")
    monkeypatch.setattr(logging, "getLogger", lambda: test_logger)

    configure_logging("INFO")
    configure_logging("INFO")

    assert len(test_logger.handlers) == 1
