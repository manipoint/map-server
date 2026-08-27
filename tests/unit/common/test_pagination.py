"""Tests for reusable cursor-pagination helpers."""

import base64
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from app.common.exceptions import InvalidCursorError
from app.common.pagination import (
    CURSOR_VERSION,
    INVALID_CURSOR_MESSAGE,
    MAX_CURSOR_LENGTH,
    PageCursor,
    decode_page_cursor,
    encode_page_cursor,
)


def encode_payload(payload: Any) -> str:
    """Encode an arbitrary test payload with the production wire format."""

    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")


def test_page_cursor_round_trip_normalizes_timestamp_to_utc() -> None:
    """A valid cursor should round-trip with a canonical UTC timestamp."""

    item_id = uuid4()
    source_timezone = timezone(timedelta(hours=5))
    cursor = PageCursor(
        updated_at=datetime(2026, 8, 26, 17, 30, tzinfo=source_timezone),
        item_id=item_id,
    )

    encoded = encode_page_cursor(cursor)
    decoded = decode_page_cursor(encoded)

    assert "=" not in encoded
    assert decoded == PageCursor(
        updated_at=datetime(2026, 8, 26, 12, 30, tzinfo=UTC),
        item_id=item_id,
    )


def test_encode_page_cursor_rejects_naive_datetime() -> None:
    """A cursor timestamp should represent one unambiguous instant."""

    cursor = PageCursor(
        updated_at=datetime(2026, 8, 26, 12, 30),
        item_id=uuid4(),
    )

    with pytest.raises(InvalidCursorError, match=INVALID_CURSOR_MESSAGE):
        encode_page_cursor(cursor)


@pytest.mark.parametrize("value", ["", "x" * (MAX_CURSOR_LENGTH + 1)])
def test_decode_page_cursor_rejects_empty_or_oversized_value(value: str) -> None:
    """Invalid cursor sizes should fail before decoding work begins."""

    with pytest.raises(InvalidCursorError, match=INVALID_CURSOR_MESSAGE):
        decode_page_cursor(value)


@pytest.mark.parametrize(
    "value",
    [
        "%%%not-base64%%%",
        base64.urlsafe_b64encode(b"not-json").decode("ascii").rstrip("="),
    ],
)
def test_decode_page_cursor_rejects_invalid_encoding_or_json(value: str) -> None:
    """Malformed transport and JSON data should share one safe error."""

    with pytest.raises(InvalidCursorError, match=INVALID_CURSOR_MESSAGE):
        decode_page_cursor(value)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {
            "v": CURSOR_VERSION,
            "updated_at": "2026-08-26T12:30:00+00:00",
            "item_id": str(uuid4()),
            "unexpected": True,
        },
        {
            "v": 2,
            "updated_at": "2026-08-26T12:30:00+00:00",
            "item_id": str(uuid4()),
        },
        {
            "v": True,
            "updated_at": "2026-08-26T12:30:00+00:00",
            "item_id": str(uuid4()),
        },
        {
            "v": CURSOR_VERSION,
            "updated_at": "not-a-date",
            "item_id": str(uuid4()),
        },
        {
            "v": CURSOR_VERSION,
            "updated_at": "2026-08-26T12:30:00",
            "item_id": str(uuid4()),
        },
        {
            "v": CURSOR_VERSION,
            "updated_at": "2026-08-26T12:30:00+00:00",
            "item_id": "not-a-uuid",
        },
    ],
)
def test_decode_page_cursor_rejects_invalid_payload(payload: Any) -> None:
    """Cursor schema, version, timestamp, and identifier should be strict."""

    with pytest.raises(InvalidCursorError, match=INVALID_CURSOR_MESSAGE) as error:
        decode_page_cursor(encode_payload(payload))

    assert str(error.value) == INVALID_CURSOR_MESSAGE
