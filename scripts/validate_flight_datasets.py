"""Validate local flight datasets without calling external services."""

import argparse
import asyncio
import sys
from pathlib import Path

from app.common.exceptions import ProviderConfigurationError
from app.providers.airports.local_provider import LocalAirportProvider
from app.providers.flights.local_metadata_provider import (
    LocalFlightMetadataProvider,
)


async def validate_datasets(
    *,
    directory_path: Path,
    metadata_path: Path,
) -> None:
    """Check dataset schemas and airport timezone coverage."""
    directory = await LocalAirportProvider.from_file(
        path=directory_path,
    )
    metadata = await LocalFlightMetadataProvider.from_file(
        path=metadata_path,
    )

    missing_codes = directory.airport_codes - metadata.airport_codes

    if missing_codes:
        raise ProviderConfigurationError(
            "Airport directory contains airports without timezone metadata: "
            + ", ".join(sorted(missing_codes))
        )

    print(
        "Flight datasets valid: "
        f"{len(directory.airport_codes)} directory airports, "
        f"{len(metadata.airport_codes)} airports with timezone metadata."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate airport and flight metadata JSON files.",
    )
    parser.add_argument(
        "--directory",
        required=True,
        type=Path,
        help="Airport directory JSON path.",
    )
    parser.add_argument(
        "--metadata",
        required=True,
        type=Path,
        help="Flight metadata JSON path.",
    )
    arguments = parser.parse_args()

    try:
        asyncio.run(
            validate_datasets(
                directory_path=arguments.directory,
                metadata_path=arguments.metadata,
            )
        )
    except ProviderConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
