"""Travel-graph exceptions."""


class ToolRoundLimitError(Exception):
    """Raised when one response exceeds its allowed tool rounds."""


class InvalidItinerarySubmissionError(ValueError):
    """Raised when the model submits an invalid itinerary handoff."""
