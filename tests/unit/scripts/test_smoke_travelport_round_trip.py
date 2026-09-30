"""Smoke-runner safety gates and real adapter flow with mocked HTTP."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.providers.flights.schemas import FlightSearchInput, FlightSearchResult
from scripts import smoke_travelport_round_trip as smoke

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "travelport"
ARGS = [
    "--origin",
    "JFK",
    "--destination",
    "LAX",
    "--departure",
    "2027-11-08",
    "--return-date",
    "2027-11-15",
]


def config(**overrides):
    return Settings(
        _env_file=None,
        flight_provider="travelport",
        flight_metadata_path=str(FIXTURES / "flight_metadata.json"),
        airport_directory_path=str(FIXTURES / "airport_directory.json"),
        travelport_username="fixture-user",
        travelport_password="fixture-secret",
        travelport_client_id="fixture-client",
        travelport_client_secret="fixture-secret",
        travelport_pcc_core="TEST",
        **overrides,
    )


def test_no_opt_in_does_not_read_settings_or_call_network(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "Settings", lambda: pytest.fail("Settings loaded"))
    assert smoke.main(ARGS) == 2
    assert "No network call" in capsys.readouterr().err


def test_production_rejected_before_any_http(monkeypatch):
    monkeypatch.setattr(
        smoke.httpx, "AsyncClient", lambda **kw: pytest.fail("HTTP created")
    )
    with pytest.raises(smoke.ProviderConfigurationError):
        asyncio.run(
            smoke.run_sandbox_search(
                settings=config(travelport_environment="production"),
                request=FlightSearchInput(
                    origin="JFK",
                    destination="LAX",
                    departure_date="2027-11-08",
                    return_date="2027-11-15",
                ),
            )
        )


def test_real_service_and_adapter_are_used_without_llm(monkeypatch, capsys):
    service_class = smoke.FlightSearchService
    monkeypatch.setattr(
        smoke,
        "FlightSearchService",
        lambda **kwargs: service_class(
            clock=lambda: datetime(2027, 1, 1, tzinfo=UTC), **kwargs
        ),
    )
    settings = config()
    monkeypatch.setattr(smoke, "Settings", lambda: settings)
    calls = []
    responses = []

    def handle(request):
        calls.append(request)
        assert ".pp.travelport." in request.url.host
        if request.url.path.endswith("/oauth/token"):
            response = httpx.Response(200, json={"access_token": "fixture-token"})
        else:
            routes = json.loads(request.content)["CatalogProductOfferingsRequest"][
                "SearchCriteriaFlight"
            ]
            assert len(routes) == 2
            response = httpx.Response(
                200, content=(FIXTURES / "round_trip_response.json").read_bytes()
            )
        responses.append(response)
        return response

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        smoke.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    )
    assert smoke.main(["--run-sandbox", *ARGS]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["offers"][0]["total_price"] == "486.44"
    assert "fixture-token" not in output and "fixture-secret" not in output
    assert len(calls) == 2
    assert all(response.is_closed for response in responses)


@pytest.mark.parametrize(
    "error,exit_code",
    [
        (smoke.ProviderConfigurationError("fixture-secret"), 2),
        (smoke.ProviderUnavailableError("fixture-secret"), 1),
        (RuntimeError("fixture-secret"), 1),
    ],
)
def test_errors_are_sanitized(monkeypatch, capsys, error, exit_code):
    monkeypatch.setattr(smoke, "Settings", lambda: object())
    monkeypatch.setattr(smoke, "run_sandbox_search", AsyncMock(side_effect=error))
    assert smoke.main(["--run-sandbox", *ARGS]) == exit_code
    output = capsys.readouterr()
    assert "fixture-secret" not in output.out + output.err


def test_empty_inventory_is_inconclusive_not_passed(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "Settings", lambda: object())
    monkeypatch.setattr(
        smoke,
        "run_sandbox_search",
        AsyncMock(
            return_value=FlightSearchResult(
                status="no_offers", searched_at=datetime.now(UTC)
            )
        ),
    )
    assert smoke.main(["--run-sandbox", *ARGS]) == 3
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_invalid_request_does_not_load_configuration(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "Settings", lambda: pytest.fail("Settings loaded"))
    assert smoke.main(["--run-sandbox", *ARGS, "--adults", "0"]) == 2
    assert "FAIL" in capsys.readouterr().err


def test_missing_return_is_rejected_before_http(monkeypatch):
    monkeypatch.setattr(
        smoke.httpx, "AsyncClient", lambda **kw: pytest.fail("HTTP created")
    )
    with pytest.raises(smoke.ProviderConfigurationError):
        asyncio.run(
            smoke.run_sandbox_search(
                settings=config(),
                request=FlightSearchInput(
                    origin="JFK", destination="LAX", departure_date="2027-11-08"
                ),
            )
        )


@pytest.mark.parametrize(
    "stage,status,expected_exit",
    [
        ("authentication", 400, 2),
        ("authentication", 401, 2),
        ("authentication", 403, 2),
        ("authentication", 429, 1),
        ("flight_search", 401, 2),
        ("flight_search", 403, 2),
        ("flight_search", 429, 1),
        ("flight_search", 500, 1),
    ],
)
def test_provider_failures_report_stage_status_without_secrets(
    monkeypatch, capsys, stage, status, expected_exit
):
    settings = config()
    monkeypatch.setattr(smoke, "Settings", lambda: settings)
    real_service = smoke.FlightSearchService
    monkeypatch.setattr(
        smoke,
        "FlightSearchService",
        lambda **kw: real_service(clock=lambda: datetime(2027, 1, 1, tzinfo=UTC), **kw),
    )

    def handle(request):
        is_auth = request.url.path.endswith("/oauth/token")
        if is_auth and stage != "authentication":
            return httpx.Response(200, json={"access_token": "PRIVATE_TOKEN"})
        return httpx.Response(
            status,
            json={"error": "PRIVATE_BODY"},
            headers={"Private-Header": "PRIVATE_HEADER"},
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        smoke.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    )
    assert smoke.main(["--run-sandbox", *ARGS]) == expected_exit
    captured = capsys.readouterr()
    events = [
        json.loads(line) for line in captured.err.splitlines() if line.startswith("{")
    ]
    assert {
        "stage": stage,
        "outcome": "response_received",
        "http_status": status,
    } in events
    assert not any(
        secret in captured.out + captured.err
        for secret in (
            "PRIVATE_TOKEN",
            "PRIVATE_BODY",
            "PRIVATE_HEADER",
            "fixture-secret",
            "fixture-user",
        )
    )


def test_transport_timeout_has_started_stage_without_fake_http_status(
    monkeypatch, capsys
):
    settings = config()
    monkeypatch.setattr(smoke, "Settings", lambda: settings)
    real_service = smoke.FlightSearchService
    monkeypatch.setattr(
        smoke,
        "FlightSearchService",
        lambda **kw: real_service(clock=lambda: datetime(2027, 1, 1, tzinfo=UTC), **kw),
    )

    def handle(request):
        raise httpx.ReadTimeout("PRIVATE_DETAIL", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        smoke.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    )
    assert smoke.main(["--run-sandbox", *ARGS]) == 1
    err = capsys.readouterr().err
    assert '"stage": "authentication", "outcome": "request_started"' in err
    assert "http_status" not in err
    assert "PRIVATE_DETAIL" not in err
