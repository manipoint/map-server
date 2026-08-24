"""Timezone-safe application clock utilities."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import TypeAlias

UtcClock: TypeAlias = Callable[[], datetime]
DateClock: TypeAlias = Callable[[], date]


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""

    return datetime.now(UTC)


def utc_today() -> date:
    """Return the current UTC calendar date."""

    return utc_now().date()
