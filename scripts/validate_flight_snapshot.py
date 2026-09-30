"""Verify exported snapshot checksums and dataset validity."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from app.common.exceptions import ProviderConfigurationError
from scripts.validate_flight_datasets import validate_datasets

SNAPSHOT_FILES = frozenset(
    {
        "airport_directory.json",
        "flight_metadata.json",
        "LICENSE-airportsdata.txt",
    }
)


def verify_snapshot_checksums(*, snapshot_path: Path) -> None:
    """Reject missing, incomplete or modified snapshot files."""
    try:
        manifest = json.loads(
            (snapshot_path / "manifest.json").read_text(
                encoding="utf-8",
            )
        )

        if not isinstance(manifest, dict):
            raise ProviderConfigurationError("Snapshot manifest must be an object")

        checksums = manifest.get("sha256")

        if not isinstance(checksums, dict) or set(checksums) != SNAPSHOT_FILES:
            raise ProviderConfigurationError(
                "Snapshot manifest contains an invalid file list"
            )

        for filename in sorted(SNAPSHOT_FILES):
            expected = checksums[filename]

            if (
                not isinstance(expected, str)
                or len(expected) != 64
                or any(character not in "0123456789abcdef" for character in expected)
            ):
                raise ProviderConfigurationError(
                    f"Invalid snapshot checksum: {filename}"
                )

            with (snapshot_path / filename).open("rb") as source:
                actual = hashlib.file_digest(
                    source,
                    "sha256",
                ).hexdigest()

            if actual != expected:
                raise ProviderConfigurationError(
                    f"Snapshot checksum mismatch: {filename}"
                )

    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProviderConfigurationError(
            "Snapshot files or manifest are unavailable or invalid"
        ) from None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a generated flight dataset snapshot.",
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
    )
    arguments = parser.parse_args()
    snapshot_path = arguments.snapshot

    try:
        verify_snapshot_checksums(snapshot_path=snapshot_path)

        asyncio.run(
            validate_datasets(
                directory_path=(snapshot_path / "airport_directory.json"),
                metadata_path=(snapshot_path / "flight_metadata.json"),
            )
        )
    except ProviderConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1

    print("Snapshot integrity verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
