"""Airport conversion rejects invalid data and preserves airline metadata."""

import pytest

from app.common.exceptions import ProviderConfigurationError
from app.providers.flights.metadata_schemas import FlightMetadata
from scripts.build_airport_datasets import build_airport_datasets


def record(**overrides):
    return {
        "iata": "LHE",
        "tz": "Asia/Karachi",
        "name": "Test Airport",
        "city": "Lahore",
        "country": "PK",
        **overrides,
    }


def test_outputs_are_sorted_and_share_codes():
    records = [record(iata="LHE"), record(iata="ISB")]
    directory, metadata = build_airport_datasets(records=iter(records))
    assert [airport.iata_code for airport in directory.airports] == ["ISB", "LHE"]
    assert list(metadata.airports) == ["ISB", "LHE"]
    reversed_result = build_airport_datasets(records=reversed(records))
    assert (directory, metadata) == reversed_result


def test_normalization_and_optional_city():
    directory, metadata = build_airport_datasets(
        records=[record(iata=" lhe ", country=" pk ", city="  ")],
    )
    (airport,) = directory.airports
    assert airport.iata_code == "LHE"
    assert airport.country_code == "PK"
    assert airport.city_name is None
    assert airport.country_name is None
    assert airport.provider_location_id == "airportsdata:LHE"
    assert metadata.airports["LHE"].time_zone == "Asia/Karachi"


def test_non_iata_records_are_skipped():
    directory, _ = build_airport_datasets(records=[{}, record(iata=" "), record()])
    assert len(directory.airports) == 1


@pytest.mark.parametrize("records", [[], [{}], [record(iata="")]])
def test_empty_import_is_rejected(records):
    with pytest.raises(ProviderConfigurationError, match="no usable IATA"):
        build_airport_datasets(records=records)


@pytest.mark.parametrize(
    "overrides",
    [
        {"iata": "123"},
        {"iata": 123},
        {"tz": "Invalid/Zone"},
        {"name": ""},
        {"country": "USA"},
        {"city": 123},
    ],
)
def test_invalid_records_fail_with_safe_position(overrides):
    with pytest.raises(ProviderConfigurationError) as error:
        build_airport_datasets(records=[{}, record(**overrides)])
    assert str(error.value) == "Invalid airport source record at position 2"
    assert error.value.__suppress_context__


def test_normalized_duplicate_codes_are_rejected():
    with pytest.raises(
        ProviderConfigurationError, match="Duplicate airport IATA code: LHE"
    ):
        build_airport_datasets(records=[record(), record(iata=" lhe ")])


def test_existing_airlines_preserved_without_mutating_input():
    existing = FlightMetadata.model_validate(
        {
            "airports": {"ISB": {"iata_code": "ISB", "time_zone": "Asia/Karachi"}},
            "airlines": {"PK": {"carrier_code": "PK", "name": "Test Airline"}},
        }
    )
    snapshot = existing.model_dump()
    _, metadata = build_airport_datasets(records=[record()], existing_metadata=existing)
    assert metadata.airlines == existing.airlines
    assert set(metadata.airports) == {"LHE"}
    metadata.airlines.clear()
    assert existing.model_dump() == snapshot
