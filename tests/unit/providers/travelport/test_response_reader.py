"""Bounded response reading with multi-chunk and compressed bodies."""

import asyncio
import gzip
from decimal import Decimal

import httpx
import pytest

from app.common.exceptions import ProviderUnavailableError
from app.providers.travelport.response_reader import read_travelport_json


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def read(body, limit, *, compressed=False):
    async def run():
        response = httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"} if compressed else {},
            stream=ChunkStream([body[:7], body[7:]]),
        )
        try:
            return await read_travelport_json(response=response, max_bytes=limit)
        finally:
            await response.aclose()

    return asyncio.run(run())


def test_multiple_chunks_are_accumulated_before_parsing():
    body = b'{"padding":"' + b"x" * 150_000 + b'","price":100.1234567890123456789}'
    result = read(body, len(body))
    assert len(result["padding"]) == 150_000
    assert result["price"] == Decimal("100.1234567890123456789")


def test_exact_limit_is_accepted_and_one_byte_over_is_rejected():
    body = b'{"value":true}'
    assert read(body, len(body)) == {"value": True}
    with pytest.raises(ProviderUnavailableError, match="size limit"):
        read(body, len(body) - 1)


def test_limit_applies_to_decompressed_bytes():
    body = b'{"value":"' + b"x" * 100_000 + b'"}'
    compressed = gzip.compress(body)
    assert len(compressed) < 1000
    with pytest.raises(ProviderUnavailableError, match="size limit"):
        read(compressed, 1000, compressed=True)


@pytest.mark.parametrize("body", [b"", b"not-json", b"\xff", b"[]", b"null", b"42"])
def test_invalid_json_or_non_object_root_is_rejected(body):
    with pytest.raises(ProviderUnavailableError):
        read(body, 1000)


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="positive"):
        read(b"{}", limit)
