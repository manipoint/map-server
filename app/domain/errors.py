"""Typed domain errors."""


class ConversationError(Exception):
    """Base exception for conversation failures."""


class ConversationNotFoundError(ConversationError):
    """Raised when a conversation is missing or owned by another user."""


class ClientMessageConflictError(ConversationError):
    """Raised when a client message ID is reused with different content."""
