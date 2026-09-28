"""Read bounded Travelport JSON responses."""

import json
from decimal import Decimal
from typing import Final

import httpx

from app.common.exceptions import ProviderUnavailableError

RESPONSE_CHUNK_SIZE_BYTES: Final[int] = 64 * 1024


async def read_travelport_json(
    *,
    response: httpx.Response,
    max_bytes: int,
) -> dict[str, object]:
    """Read decoded response bytes without exceeding the configured limit."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=RESPONSE_CHUNK_SIZE_BYTES):
        if len(body) + len(chunk) > max_bytes:
            raise ProviderUnavailableError(
                "Travelport response exceeds the configured size limit"
            )
        body.extend(chunk)

    try:
        payload = json.loads(
            body,
            parse_float=Decimal,
        )

    except (ValueError, UnicodeError):
        raise ProviderUnavailableError("Travelport returned invalid JSON") from None

    if not isinstance(payload, dict):
        raise ProviderUnavailableError(
            "Travelport returned an invalid response envelope"
        )
    return payload
