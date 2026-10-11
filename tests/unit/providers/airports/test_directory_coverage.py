"""Coverage inventories must reflect immutable provider snapshots."""

import asyncio

from app.providers.airports.directory_schemas import AirportDirectory
from app.providers.airports.local_provider import LocalAirportProvider
from app.providers.airports.schemas import AirportSearchInput
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


def test_military_air_base_is_not_offered_for_passenger_city_lookup() -> None:
    directory = AirportDirectory.model_validate(
        {
            "airports": [
                {
                    "provider_location_id": "local:DXB",
                    "iata_code": "DXB",
                    "location_type": "airport",
                    "name": "Dubai International Airport",
                    "city_name": "Dubai",
                    "country_code": "AE",
                },
                {
                    "provider_location_id": "local:NHD",
                    "iata_code": "NHD",
                    "location_type": "airport",
                    "name": "Al Minhad Air Base",
                    "city_name": "Dubai",
                    "country_code": "AE",
                },
            ]
        }
    )
    provider = LocalAirportProvider(directory=directory)

    city = asyncio.run(
        provider.search_airports(request=AirportSearchInput(query="Dubai"))
    )
    military_code = asyncio.run(
        provider.search_airports(request=AirportSearchInput(query="NHD"))
    )

    assert [option.iata_code for option in city.options] == ["DXB"]
    assert military_code.options == []
    assert provider.airport_codes == frozenset({"DXB"})


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
