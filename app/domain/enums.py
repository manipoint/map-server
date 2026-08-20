"""Domain enumerations."""

from enum import StrEnum


class AssistantRunStatus(StrEnum):
    """Lifecycle states for one assistant-generation job."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TravelResponseErrorCode(StrEnum):
    """Safe failure codes exposed by travel-response workflows."""

    PROVIDER_ERROR = "provider_error"
    GENERATION_FAILED = "generation_failed"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
