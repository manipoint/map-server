"""Domain enumerations."""

from enum import StrEnum


class AssistantRunStatus(StrEnum):
    """Lifecycle states for one assistant-generation job."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
