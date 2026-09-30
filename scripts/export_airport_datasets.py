"""Export validated airport datasets into a new snapshot directory."""

import argparse
import hashlib
import json
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path
from tempfile import mkdtemp

import airportsdata
from pydantic import ValidationError

from app.common.exceptions import ProviderConfigurationError
from app.providers.flights.metadata_schemas import FlightMetadata
from scripts.build_airport_datasets import build_airport_datasets


def export_datasets(
    *,
    output_parent: Path,
    existing_metadata_path: Path | None = None,
) -> Path:
    """Export one complete snapshot without modifying existing datasets."""
    existing_metadata = None

    if existing_metadata_path is not None:
        existing_metadata = FlightMetadata.model_validate_json(
            existing_metadata_path.read_text(encoding="utf-8")
        )

    directory, metadata = build_airport_datasets(
        records=airportsdata.load().values(),
        existing_metadata=existing_metadata,
    )

    package = distribution("airportsdata")
    license_files = [entry for entry in package.files or () if entry.name == "LICENSE"]

    if len(license_files) != 1:
        raise ProviderConfigurationError(
            "Cannot identify the airportsdata license file"
        )

    license_text = package.locate_file(license_files[0]).read_text(encoding="utf-8")

    documents = {
        "airport_directory.json": directory.model_dump_json(indent=2),
        "flight_metadata.json": metadata.model_dump_json(indent=2),
        "LICENSE-airportsdata.txt": license_text,
    }

    output_parent.mkdir(parents=True, exist_ok=True)
    output = Path(
        mkdtemp(
            prefix=f"airportsdata-{package.version}-",
            dir=output_parent,
        )
    )

    try:
        checksums: dict[str, str] = {}

        for filename, content in documents.items():
            data = (content.rstrip() + "\n").encode("utf-8")
            (output / filename).write_bytes(data)
            checksums[filename] = hashlib.sha256(data).hexdigest()

        manifest = {
            "source": "https://github.com/mborsetti/airportsdata",
            "source_version": package.version,
            "airport_count": len(directory.airports),
            "airline_count": len(metadata.airlines),
            "airline_source": (
                "preserved_from_existing_metadata"
                if existing_metadata is not None
                else "not_configured"
            ),
            "sha256": checksums,
        }

        # Publish only a complete manifest; both paths share a filesystem.
        temporary_manifest = output / "manifest.json.tmp"
        temporary_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_manifest.replace(output / "manifest.json")
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise

    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export airport directory and timezone metadata."
    )
    parser.add_argument(
        "--output-parent",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--existing-metadata",
        type=Path,
        default=None,
        help="Optional metadata file whose airline entries are preserved.",
    )
    arguments = parser.parse_args()

    try:
        output = export_datasets(
            output_parent=arguments.output_parent,
            existing_metadata_path=arguments.existing_metadata,
        )
    except (OSError, UnicodeError, ValidationError):
        print(
            "Export failed: check input files and output permissions.",
            file=sys.stderr,
        )
        return 1
    except ProviderConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(f"Dataset snapshot created: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
