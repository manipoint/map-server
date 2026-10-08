"""Snapshot integrity checks and CLI schema validation."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.common.exceptions import ProviderConfigurationError
from scripts.validate_flight_snapshot import SNAPSHOT_FILES, verify_snapshot_checksums

ROOT = Path(__file__).resolve().parents[3]


def write_manifest(path):
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "sha256": {
                    name: hashlib.sha256((path / name).read_bytes()).hexdigest()
                    for name in SNAPSHOT_FILES
                },
            }
        )
    )


@pytest.fixture
def snapshot(tmp_path):
    fixtures = ROOT / "tests/fixtures/airports"
    for name in ("airport_directory.json", "flight_metadata.json"):
        (tmp_path / name).write_bytes((fixtures / name).read_bytes())
    (tmp_path / "LICENSE-airportsdata.txt").write_text("Synthetic license\n")
    write_manifest(tmp_path)
    return tmp_path


def test_valid_snapshot(snapshot):
    verify_snapshot_checksums(snapshot_path=snapshot)


@pytest.mark.parametrize("name", sorted(SNAPSHOT_FILES))
def test_tampered_file_rejected(snapshot, name):
    path = snapshot / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ProviderConfigurationError, match="checksum mismatch"):
        verify_snapshot_checksums(snapshot_path=snapshot)


@pytest.mark.parametrize("name", ["manifest.json", *sorted(SNAPSHOT_FILES)])
def test_missing_file_rejected(snapshot, name):
    (snapshot / name).unlink()
    with pytest.raises(ProviderConfigurationError, match="unavailable or invalid"):
        verify_snapshot_checksums(snapshot_path=snapshot)


@pytest.mark.parametrize("content", [b"{bad", b"\xff", b"[]", b"{}", b'{"sha256": []}'])
def test_invalid_manifest_rejected(snapshot, content):
    (snapshot / "manifest.json").write_bytes(content)
    with pytest.raises(ProviderConfigurationError):
        verify_snapshot_checksums(snapshot_path=snapshot)


@pytest.mark.parametrize("checksum", [None, 42, "a" * 63, "g" * 64, "A" * 64])
def test_invalid_digest_rejected(snapshot, checksum):
    path = snapshot / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["sha256"]["airport_directory.json"] = checksum
    path.write_text(json.dumps(manifest))
    with pytest.raises(ProviderConfigurationError, match="Invalid snapshot checksum"):
        verify_snapshot_checksums(snapshot_path=snapshot)


def test_unexpected_manifest_filename_rejected(snapshot):
    path = snapshot / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["sha256"]["../outside.json"] = "a" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ProviderConfigurationError, match="invalid file list"):
        verify_snapshot_checksums(snapshot_path=snapshot)


@pytest.mark.parametrize("scenario", ["valid", "tampered", "schema", "coverage"])
def test_cli_checks_integrity_before_reporting_success(snapshot, scenario):
    metadata = snapshot / "flight_metadata.json"
    if scenario == "tampered":
        metadata.write_bytes(metadata.read_bytes() + b" ")
    elif scenario in {"schema", "coverage"}:
        metadata.write_text(
            "[]" if scenario == "schema" else '{"airports": {}, "airlines": {}}'
        )
        write_manifest(snapshot)  # Valid hashes must not bypass model/coverage checks.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.validate_flight_snapshot",
            "--snapshot",
            str(snapshot),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == (0 if scenario == "valid" else 1)
    assert "Traceback" not in result.stderr
    if scenario == "valid":
        assert "Flight datasets valid:" in result.stdout
        assert result.stdout.endswith("Snapshot integrity verified.\n")
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr
