"""Coverage inventories must reflect immutable provider snapshots."""

from app.providers.airports.directory_schemas import AirportDirectory
from app.providers.airports.local_provider import LocalAirportProvider
from app.providers.flights.local_metadata_provider import LocalFlightMetadataProvider
from app.providers.flights.metadata_schemas import FlightMetadata


def test_directory_codes_survive_input_mutation() -> None:
    directory = AirportDirectory.model_validate(
        {
            "airports": [
                {
                    "provider_location_id": "local:LHE",
                    "iata_code": "LHE",
                    "location_type": "airport",
                    "name": "Lahore Airport",
                    "country_code": "PK",
                }
            ],
        }
    )
    provider = LocalAirportProvider(directory=directory)
    directory.airports.clear()

    assert provider.airport_codes == frozenset({"LHE"})
    assert isinstance(provider.airport_codes, frozenset)


def test_metadata_codes_survive_input_mutation() -> None:
    metadata = FlightMetadata.model_validate(
        {
            "airports": {"LHE": {"iata_code": "LHE", "time_zone": "Asia/Karachi"}},
        }
    )
    provider = LocalFlightMetadataProvider(metadata=metadata)
    metadata.airports.clear()

    assert provider.airport_codes == frozenset({"LHE"})
    assert isinstance(provider.airport_codes, frozenset)
