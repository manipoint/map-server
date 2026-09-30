"""Baseline request safety and sanitized live-runner behavior."""

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from scripts import smoke_travelport_baseline as smoke


def test_no_opt_in_does_not_load_settings(monkeypatch):
    monkeypatch.setattr(smoke, "Settings", lambda: pytest.fail("Settings read"))
    assert smoke.main(["--departure", "2030-10-30"]) == 2


def test_production_rejected_before_http(monkeypatch):
    monkeypatch.setattr(smoke.httpx, "AsyncClient", lambda **kw: pytest.fail("Network"))
    assert (
        asyncio.run(
            smoke.run_baseline(
                SimpleNamespace(travelport_environment="production"), date(2030, 10, 30)
            )
        )
        == 2
    )


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_exact_baseline_request_and_safe_failure(monkeypatch, capsys, status):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["TVP-PCC-Core"] == "UM2_1G"
        import json

        body = json.loads(request.content)["CatalogProductOfferingsRequest"]
        assert body["contentSourceList"] == ["NDC"]
        assert body["maxNumberOfUpsellsToReturn"] == 4
        assert len(body["SearchCriteriaFlight"]) == 1
        assert body["SearchModifiersAir"]["CarrierPreference"][0]["carriers"] == ["AA"]
        return httpx.Response(status, text="PRIVATE_BODY")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        smoke.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    )
    monkeypatch.setattr(
        smoke,
        "TravelportAuthClient",
        lambda **kw: SimpleNamespace(
            get_access_token=AsyncMock(return_value=SecretStr("PRIVATE_TOKEN"))
        ),
    )
    settings = SimpleNamespace(
        travelport_environment="preproduction",
        travelport_air_base_url="https://api.pp.travelport.net/11/air",
        provider_timeout_seconds=5,
    )
    assert asyncio.run(smoke.run_baseline(settings, date(2030, 10, 30))) == 1
    output = capsys.readouterr().out
    assert str(status) in output
    assert "PRIVATE_BODY" not in output and "PRIVATE_TOKEN" not in output
    assert len(calls) == 1
