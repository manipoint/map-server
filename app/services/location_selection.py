"""Shared deterministic location-selection rules."""

from collections.abc import Sequence

from app.common.exceptions import AmbiguousLocationError, LocationNotFoundError
from app.providers.locations.schemas import ResolvedLocation


def select_resolved_location(
    *,
    query: str,
    candidates: Sequence[ResolvedLocation],
) -> ResolvedLocation:
    """Select one exact or unambiguous location candidate."""

    if not candidates:
        raise LocationNotFoundError("No location matched the destination")

    selected = candidates[0]
    if len(candidates) > 1 and query.casefold() != selected.display_name.casefold():
        raise AmbiguousLocationError(
            candidates=[candidate.display_name for candidate in candidates]
        )

    return selected
