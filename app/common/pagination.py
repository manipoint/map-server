"""Reusable cursor-pagination helpers."""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from app.common.exceptions import InvalidCursorError

CURSOR_VERSION: Final = 1
MAX_CURSOR_LENGTH: Final = 512
INVALID_CURSOR_MESSAGE: Final = "Pagination cursor is invalid"


@dataclass(frozen=True, slots=True)
class PageCursor:
    """Stable position within an updated-at ordered collection."""

    updated_at: datetime
    item_id: UUID


def encode_page_cursor(cursor: PageCursor) -> str:
    """Encode a page cursor as compact URL-safe Base64 text."""

    if cursor.updated_at.utcoffset() is None:
        raise InvalidCursorError(INVALID_CURSOR_MESSAGE)

    payload = {
        "v": CURSOR_VERSION,
        "updated_at": cursor.updated_at.astimezone(UTC).isoformat(),
        "item_id": str(cursor.item_id),
    }
    encoded_payload = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return base64.urlsafe_b64encode(encoded_payload).decode("ascii").rstrip("=")


def decode_page_cursor(value: str) -> PageCursor:
    """Decode and validate a URL-safe pagination cursor."""

    if not value or len(value) > MAX_CURSOR_LENGTH:
        raise InvalidCursorError(INVALID_CURSOR_MESSAGE)

    padding = "=" * (-len(value) % 4)

    try:
        decoded_payload = base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded_payload)

        if not isinstance(payload, dict):
            raise ValueError

        if set(payload) != {"v", "updated_at", "item_id"}:
            raise ValueError

        version = payload["v"]
        if type(version) is not int or version != CURSOR_VERSION:
            raise ValueError

        updated_at = datetime.fromisoformat(payload["updated_at"])
        if updated_at.utcoffset() is None:
            raise ValueError

        item_id = UUID(payload["item_id"])
    except (
        binascii.Error,
        UnicodeEncodeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise InvalidCursorError(INVALID_CURSOR_MESSAGE) from error

    return PageCursor(
        updated_at=updated_at.astimezone(UTC),
        item_id=item_id,
    )
