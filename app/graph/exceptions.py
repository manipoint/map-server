"""Travel-graph exceptions."""


class ToolRoundLimitError(Exception):
    """Raised when one response exceeds its allowed tool rounds."""
