"""Tests for shared timezone-safe clock utilities."""

from datetime import UTC, datetime

from app.common.time import utc_now, utc_today


def test_utc_now_returns_current_aware_datetime() -> None:
    """The shared application clock should be aware and close to real UTC."""

    before = datetime.now(UTC)
    current = utc_now()
    after = datetime.now(UTC)

    assert current.utcoffset() == UTC.utcoffset(current)
    assert before <= current <= after


def test_utc_today_returns_current_utc_date() -> None:
    """The shared date clock should derive its date from UTC."""

    before = datetime.now(UTC).date()
    current = utc_today()
    after = datetime.now(UTC).date()

    assert current in {before, after}
