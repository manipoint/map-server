"""Snapshot export integrity, preservation and failure handling."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.common.exceptions import ProviderConfigurationError
from app.providers.airports.directory_schemas import AirportDirectory
from app.providers.flights.metadata_schemas import FlightMetadata
from scripts import export_airport_datasets as exporter


@pytest.fixture
def source(monkeypatch, tmp_path):
    license_path = tmp_path / "LICENSE"
    license_path.write_text("Synthetic test license\n", encoding="utf-8")
    package = SimpleNamespace(
        version="test-version",
        files=[Path("LICENSE")],
        locate_file=lambda entry: license_path,
    )
    monkeypatch.setattr(exporter, "distribution", lambda name: package)
    monkeypatch.setattr(
        exporter.airportsdata,
        "load",
        lambda: {
            "OPLA": {
                "iata": "LHE",
                "name": "Test Airport",
                "city": "Lahore",
                "country": "PK",
                "tz": "Asia/Karachi",
            },
        },
    )
    return package


def test_export_roundtrips_and_manifest_matches_bytes(source, tmp_path):
    output = exporter.export_datasets(output_parent=tmp_path / "snapshots")
    directory = AirportDirectory.model_validate_json(
        (output / "airport_directory.json").read_bytes()
    )
    metadata = FlightMetadata.model_validate_json(
        (output / "flight_metadata.json").read_bytes()
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert [entry.iata_code for entry in directory.airports] == ["LHE"]
    assert set(metadata.airports) == {"LHE"}
    assert metadata.airlines == {}
    assert manifest["source_version"] == "test-version"
    assert manifest["airport_count"] == 1
    assert manifest["airline_count"] == 0
    assert manifest["airline_source"] == "not_configured"
    assert set(manifest["sha256"]) == {
        "airport_directory.json",
        "flight_metadata.json",
        "LICENSE-airportsdata.txt",
    }
    for name, checksum in manifest["sha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == checksum
    assert (
        output / "LICENSE-airportsdata.txt"
    ).read_text() == "Synthetic test license\n"


def test_preserves_airlines_and_existing_snapshots(source, tmp_path):
    existing = tmp_path / "existing.json"
    existing.write_text(
        json.dumps(
            {
                "airports": {},
                "airlines": {"PK": {"carrier_code": "PK", "name": "Test Airline"}},
            }
        )
    )
    original = existing.read_bytes()
    parent = tmp_path / "snapshots"
    first = exporter.export_datasets(
        output_parent=parent, existing_metadata_path=existing
    )
    first_files = {path.name: path.read_bytes() for path in first.iterdir()}
    second = exporter.export_datasets(
        output_parent=parent, existing_metadata_path=existing
    )
    assert first != second
    assert first_files == {path.name: path.read_bytes() for path in first.iterdir()}
    assert first_files == {path.name: path.read_bytes() for path in second.iterdir()}
    assert existing.read_bytes() == original
    metadata = FlightMetadata.model_validate_json(first_files["flight_metadata.json"])
    assert metadata.airlines["PK"].name == "Test Airline"
    manifest = json.loads(first_files["manifest.json"])
    assert manifest["airline_count"] == 1
    assert manifest["airline_source"] == "preserved_from_existing_metadata"


@pytest.mark.parametrize("files", [None, [], [Path("a/LICENSE"), Path("b/LICENSE")]])
def test_missing_or_ambiguous_license_rejected_before_output(source, tmp_path, files):
    source.files = files
    parent = tmp_path / "snapshots"
    with pytest.raises(ProviderConfigurationError, match="license"):
        exporter.export_datasets(output_parent=parent)
    assert not parent.exists()


@pytest.mark.parametrize("filename", ["flight_metadata.json", "manifest.json.tmp"])
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_failed_write_cleans_only_new_snapshot(
    source, tmp_path, monkeypatch, filename, error_type
):
    parent = tmp_path / "snapshots"
    previous = exporter.export_datasets(output_parent=parent)
    before = {path.name: path.read_bytes() for path in previous.iterdir()}
    method = "write_text" if filename == "manifest.json.tmp" else "write_bytes"
    original = getattr(Path, method)

    def fail(path, *args, **kwargs):
        if path.name == filename:
            raise error_type("simulated failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, fail)
    with pytest.raises(error_type, match="simulated failure"):
        exporter.export_datasets(output_parent=parent)
    assert list(parent.iterdir()) == [previous]
    assert before == {path.name: path.read_bytes() for path in previous.iterdir()}


@pytest.mark.parametrize("fail_replace", [False, True])
def test_manifest_is_complete_before_publication(
    source, tmp_path, monkeypatch, fail_replace
):
    parent = tmp_path / "snapshots"
    previous = exporter.export_datasets(output_parent=parent)
    before = {path.name: path.read_bytes() for path in previous.iterdir()}
    original = Path.replace
    publications = []

    def replace(path, target):
        assert path.name == "manifest.json.tmp"
        assert target == path.parent / "manifest.json"
        assert not target.exists()
        manifest = json.loads(path.read_text())
        for name, checksum in manifest["sha256"].items():
            assert (
                hashlib.sha256((path.parent / name).read_bytes()).hexdigest()
                == checksum
            )
        publications.append(target)
        if fail_replace:
            raise OSError("simulated rename failure")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    if fail_replace:
        with pytest.raises(OSError, match="rename failure"):
            exporter.export_datasets(output_parent=parent)
        assert list(parent.iterdir()) == [previous]
    else:
        output = exporter.export_datasets(output_parent=parent)
        assert (output / "manifest.json").is_file()
        assert not (output / "manifest.json.tmp").exists()
    assert len(publications) == 1
    assert before == {path.name: path.read_bytes() for path in previous.iterdir()}


@pytest.mark.parametrize("invalid_input", [False, True])
def test_cli_success_and_sanitized_failure(
    source, tmp_path, monkeypatch, capsys, invalid_input
):
    argv = ["export_airport_datasets", "--output-parent", str(tmp_path / "snapshots")]
    if invalid_input:
        existing = tmp_path / "bad.json"
        existing.write_text("{private-invalid-content")
        argv += ["--existing-metadata", str(existing)]
    monkeypatch.setattr(exporter.sys, "argv", argv)
    assert exporter.main() == (1 if invalid_input else 0)
    captured = capsys.readouterr()
    if invalid_input:
        assert captured.out == ""
        assert "Export failed" in captured.err
        assert "private-invalid-content" not in captured.err
        assert not (tmp_path / "snapshots").exists()
    else:
        assert "Dataset snapshot created:" in captured.out
        assert captured.err == ""
