"""Tests for shared domain enumerations."""

from app.domain.enums import AssistantRunStatus


def test_assistant_run_status_values_match_persisted_values() -> None:
    """Processing states should remain stable across code and database records."""

    assert [status.value for status in AssistantRunStatus] == [
        "processing",
        "completed",
        "failed",
    ]
