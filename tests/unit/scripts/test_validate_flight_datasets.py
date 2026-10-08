"""Dataset validation uses real loaders and predictable CLI outcomes."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.common.exceptions import ProviderConfigurationError
from scripts.validate_flight_datasets import validate_datasets

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "airports"


@pytest.fixture
def datasets(tmp_path):
    paths = {}
    for name, filename in (
        ("directory", "airport_directory.json"),
        ("metadata", "flight_metadata.json"),
    ):
        path = tmp_path / filename
        path.write_bytes((FIXTURES / filename).read_bytes())
        paths[name] = path
    return paths


def validate(paths):
    asyncio.run(
        validate_datasets(
            directory_path=paths["directory"],
            metadata_path=paths["metadata"],
        )
    )


@pytest.mark.parametrize("extra_metadata", [False, True])
def test_valid_coverage_accepts_extra_metadata(datasets, capsys, extra_metadata):
    if extra_metadata:
        payload = json.loads(datasets["metadata"].read_text())
        payload["airports"]["LHE"] = {
            "iata_code": "LHE",
            "time_zone": "Asia/Karachi",
        }
        datasets["metadata"].write_text(json.dumps(payload))
    validate(datasets)
    output = capsys.readouterr()
    count = 4 if extra_metadata else 3
    assert output.out == (
        f"Flight datasets valid: 3 directory airports, {count} airports with timezone metadata.\n"
    )
    assert output.err == ""


@pytest.mark.parametrize("name", ["directory", "metadata"])
@pytest.mark.parametrize("failure", ["missing", "json", "schema", "encoding"])
def test_bad_files_fail_without_success_output(datasets, capsys, name, failure):
    path = datasets[name]
    if failure == "missing":
        path.unlink()
    else:
        path.write_bytes(
            {"json": b"{bad", "schema": b"[]", "encoding": b"\xff"}[failure]
        )
    with pytest.raises(ProviderConfigurationError, match="unavailable or invalid"):
        validate(datasets)
    assert capsys.readouterr().out == ""


def test_missing_codes_are_reported_in_stable_order(datasets, capsys):
    datasets["metadata"].write_text('{"airports": {}, "airlines": {}}')
    with pytest.raises(ProviderConfigurationError, match="BUR, JFK, LAX$"):
        validate(datasets)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "scenario,expected_code", [("valid", 0), ("missing", 1), ("arguments", 2)]
)
def test_module_cli_exit_codes(datasets, scenario, expected_code):
    command = [sys.executable, "-m", "scripts.validate_flight_datasets"]
    if scenario != "arguments":
        command += [
            "--directory",
            str(datasets["directory"]),
            "--metadata",
            str(datasets["metadata"]),
        ]
    if scenario == "missing":
        datasets["metadata"].unlink()
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == expected_code
    assert "Traceback" not in result.stderr
    if scenario == "valid":
        assert "Flight datasets valid" in result.stdout
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr
