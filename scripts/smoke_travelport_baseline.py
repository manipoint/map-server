"""Reproduce the supplied one-way AA/NDC sandbox request without an LLM."""

import argparse
import asyncio
import json
from datetime import date

import httpx

from app.config import Settings
from app.providers.travelport.auth_client import TravelportAuthClient
from app.providers.travelport.flight_response_decoder import decode_travelport_search
from app.providers.travelport.response_reader import read_travelport_json


def baseline_payload(departure: date) -> dict:
    return {
        "@type": "CatalogProductOfferingsQueryRequest",
        "CatalogProductOfferingsRequest": {
            "@type": "CatalogProductOfferingsRequestAir",
            "maxNumberOfUpsellsToReturn": 4,
            "contentSourceList": ["NDC"],
            "PassengerCriteria": [
                {"@type": "PassengerCriteria", "number": 1, "passengerTypeCode": "ADT"}
            ],
            "SearchCriteriaFlight": [
                {
                    "@type": "SearchCriteriaFlight",
                    "departureDate": departure.isoformat(),
                    "From": {"value": "JFK"},
                    "To": {"value": "LAX"},
                }
            ],
            "SearchModifiersAir": {
                "@type": "SearchModifiersAir",
                "CarrierPreference": [
                    {
                        "@type": "CarrierPreference",
                        "preferenceType": "Preferred",
                        "carriers": ["AA"],
                    }
                ],
            },
        },
    }


async def run_baseline(settings: Settings, departure: date) -> int:
    if settings.travelport_environment != "preproduction":
        print("FAIL: preproduction required; no network call made.")
        return 2
    if departure < date.today():
        print("FAIL: use a future departure date; no network call made.")
        return 2
    stage = "authentication"
    try:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            auth = TravelportAuthClient(http_client=http, settings=settings)
            token = await auth.get_access_token()
            print(json.dumps({"stage": stage, "outcome": "passed"}))
            stage = "flight_search"
            async with http.stream(
                "POST",
                settings.travelport_air_base_url
                + "/catalog/search/catalogproductofferings",
                json=baseline_payload(departure),
                headers={
                    "Authorization": f"Bearer {token.get_secret_value()}",
                    "TVP-PCC-Core": "UM2_1G",
                    "TraceId": "roamly-baseline-test",
                    "Accept-Encoding": "gzip, deflate",
                },
                timeout=settings.provider_timeout_seconds,
            ) as response:
                print(json.dumps({"stage": stage, "http_status": response.status_code}))
                if response.is_error:
                    return 1
                stage = "response_validation"
                decoded = decode_travelport_search(
                    await read_travelport_json(
                        response=response,
                        max_bytes=settings.travelport_max_response_bytes,
                    )
                )
                count = len(decoded.catalog.offerings)
                print(
                    json.dumps(
                        {
                            "stage": stage,
                            "outcome": "passed" if count else "inconclusive",
                            "catalog_count": count,
                        }
                    )
                )
                return 0 if count else 3
    except Exception:
        print(json.dumps({"stage": stage, "outcome": "failed"}))
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--departure", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    if not args.run_sandbox:
        print("No network call made. Add --run-sandbox to opt in.")
        return 2
    try:
        settings = Settings()
    except Exception:
        print("FAIL: invalid settings; no values printed.")
        return 2
    return asyncio.run(run_baseline(settings, args.departure))


if __name__ == "__main__":
    raise SystemExit(main())
