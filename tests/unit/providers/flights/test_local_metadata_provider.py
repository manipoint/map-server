"""Local metadata file loading and snapshot isolation."""

import asyncio

import pytest

from app.common.exceptions import ProviderConfigurationError
from app.providers.flights.local_metadata_provider import LocalFlightMetadataProvider
from app.providers.flights.metadata_schemas import FlightMetadata


def metadata():
    return FlightMetadata(
        airports={"LHE": {"iata_code": "LHE", "time_zone": "Asia/Karachi"}},
        airlines={"AA": {"carrier_code": "AA", "name": "American Airlines"}},
    )


def test_file_is_loaded_once_and_unknown_codes_remain_absent(tmp_path):
    async def run():
        path = tmp_path / "metadata.json"
        path.write_text(metadata().model_dump_json(), encoding="utf-8")
        provider = await LocalFlightMetadataProvider.from_file(path=path)
        path.unlink()
        result = await provider.resolve(
            airport_codes=frozenset({"LHE", "JFK"}),
            carrier_codes=frozenset({"AA", "BA"}),
        )
        assert set(result.airports) == {"LHE"}
        assert set(result.airlines) == {"AA"}
        assert (
            await provider.resolve(airport_codes=frozenset(), carrier_codes=frozenset())
            == FlightMetadata()
        )

    asyncio.run(run())


def test_input_and_result_mutations_do_not_change_provider_snapshot():
    async def run():
        original = metadata()
        provider = LocalFlightMetadataProvider(metadata=original)
        original.airports.clear()
        original.airlines.clear()
        first = await provider.resolve(
            airport_codes=frozenset({"LHE"}), carrier_codes=frozenset({"AA"})
        )
        first.airports.clear()
        first.airlines.clear()
        second = await provider.resolve(
            airport_codes=frozenset({"LHE"}), carrier_codes=frozenset({"AA"})
        )
        assert second == metadata()

    asyncio.run(run())


@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b"\xff",
        b'{"airports":{"JFK":{"iata_code":"LHE","time_zone":"Asia/Karachi"}}}',
        b'{"airports":{"LHE":{"iata_code":"LHE","time_zone":"Invalid/Zone"}}}',
    ],
)
def test_invalid_file_returns_sanitized_configuration_error(tmp_path, content):
    path = tmp_path / "metadata.json"
    path.write_bytes(content)
    with pytest.raises(
        ProviderConfigurationError, match="unavailable or invalid"
    ) as caught:
        asyncio.run(LocalFlightMetadataProvider.from_file(path=path))
    assert caught.value.__suppress_context__


def test_missing_file_returns_configuration_error(tmp_path):
    with pytest.raises(ProviderConfigurationError):
        asyncio.run(
            LocalFlightMetadataProvider.from_file(path=tmp_path / "missing.json")
        )
