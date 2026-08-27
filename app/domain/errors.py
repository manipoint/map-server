"""Typed domain errors."""


class ConversationError(Exception):
    """Base exception for conversation failures."""


class ConversationNotFoundError(ConversationError):
    """Raised when a conversation is missing or owned by another user."""


class ClientMessageConflictError(ConversationError):
    """Raised when a client message ID is reused with different content."""


class TripError(Exception):
    """Base exception for trip-management failures."""


class TripNotFoundError(TripError):
    """Raised when a trip is missing or owned by another user."""


class InvalidTripDetailsError(TripError):
    """Raised when merged trip details violate business rules."""


class InvalidTripStatusTransitionError(TripError):
    """Raised when a trip status transition is not allowed."""
