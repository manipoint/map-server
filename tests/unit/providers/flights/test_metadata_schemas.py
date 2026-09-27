"""Flight metadata validation and lookup integrity."""

import pytest
from pydantic import ValidationError

from app.providers.flights.metadata_schemas import (
    AirlineMetadata,
    AirportMetadata,
    FlightMetadata,
)


def test_airport_normalizes_iata_and_preserves_timezone():
    airport = AirportMetadata(iata_code=" lhe ", time_zone=" Asia/Karachi ")
    assert airport.iata_code == "LHE"
    assert airport.time_zone == "Asia/Karachi"


@pytest.mark.parametrize("zone", ["", " ", "Invalid/Timezone", "../UTC", "/etc/passwd"])
def test_invalid_timezone_is_rejected(zone):
    with pytest.raises(ValidationError) as caught:
        AirportMetadata(iata_code="LHE", time_zone=zone)
    assert any(error["loc"] == ("time_zone",) for error in caught.value.errors())


@pytest.mark.parametrize("code", ["", "LH", "LHES", "L1E"])
def test_invalid_airport_code_is_rejected(code):
    with pytest.raises(ValidationError) as caught:
        AirportMetadata(iata_code=code, time_zone="Asia/Karachi")
    assert any(
        error["loc"] == ("iata_code",) and error["type"] != "extra_forbidden"
        for error in caught.value.errors()
    )


def test_airline_normalizes_code_and_name():
    airline = AirlineMetadata(carrier_code=" aa ", name=" American Airlines ")
    assert airline.carrier_code == "AA"
    assert airline.name == "American Airlines"


@pytest.mark.parametrize("name", ["", " "])
def test_blank_airline_names_are_rejected(name):
    with pytest.raises(ValidationError):
        AirlineMetadata(carrier_code="AA", name=name)


def test_metadata_defaults_are_independent_and_missing_codes_stay_missing():
    first, second = FlightMetadata(), FlightMetadata()
    first.airlines["AA"] = AirlineMetadata(carrier_code="AA", name="American Airlines")
    assert second.airlines == {}
    assert second.airports == {}


def test_airport_lookup_key_must_match_embedded_code():
    with pytest.raises(ValidationError):
        FlightMetadata(
            airports={"JFK": {"iata_code": "LHE", "time_zone": "Asia/Karachi"}}
        )


def test_airline_lookup_key_must_match_embedded_code():
    with pytest.raises(ValidationError):
        FlightMetadata(
            airlines={
                "BA": AirlineMetadata(carrier_code="AA", name="American Airlines")
            }
        )


def test_valid_metadata_round_trip():
    result = FlightMetadata(
        airports={"LHE": AirportMetadata(iata_code="LHE", time_zone="Asia/Karachi")},
        airlines={"AA": AirlineMetadata(carrier_code="AA", name="American Airlines")},
    )
    assert FlightMetadata.model_validate_json(result.model_dump_json()) == result


def test_airline_metadata_is_frozen():
    airline = AirlineMetadata(carrier_code="AA", name="American Airlines")
    with pytest.raises(ValidationError, match="frozen"):
        airline.carrier_code = "BA"
