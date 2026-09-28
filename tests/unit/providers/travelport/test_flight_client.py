"""Travelport transport and authentication integration without live APIs."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import ProviderConfigurationError, ProviderUnavailableError
from app.config import Settings
from app.providers.flights.metadata_provider import FlightMetadataProvider
from app.providers.flights.schemas import FlightSearchInput
from app.providers.travelport.auth_client import TravelportAuthClient
from app.providers.travelport.flight_client import TravelportFlightClient

EMPTY_RESULT = {
    "CatalogProductOfferingsResponse": {
        "CatalogProductOfferings": {"CatalogProductOffering": []}
    }
}


def settings(**overrides):
    values = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr("postgresql+asyncpg://test:test@localhost/test"),
        "jwt_signing_key": SecretStr("j" * 32),
        "refresh_token_hash_key": SecretStr("r" * 32),
        "travelport_username": SecretStr("test-user"),
        "travelport_password": SecretStr("test-password"),
        "travelport_client_id": SecretStr("test-client"),
        "travelport_client_secret": SecretStr("test-secret"),
        "travelport_pcc_core": "TEST_1G",
    }
    values.update(overrides)
    return Settings(**values)


def request(**overrides):
    return FlightSearchInput(
        origin="LHE", destination="NRT", departure_date="2027-11-07", **overrides
    )


def client(http, config=None, auth=None, metadata=None):
    config = config or settings()
    return TravelportFlightClient(
        http_client=http,
        settings=config,
        auth_client=auth or TravelportAuthClient(http_client=http, settings=config),
        metadata_provider=metadata or AsyncMock(spec=FlightMetadataProvider),
        clock=lambda: datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_real_auth_and_search_pipeline_reuses_token_and_sends_headers():
    async def run():
        calls, responses = [], []

        def handle(req):
            calls.append(req)
            response = httpx.Response(
                200,
                json={"access_token": "test-token"}
                if req.url.path.endswith("/oauth/token")
                else EMPTY_RESULT,
            )
            responses.append(response)
            return response

        metadata = AsyncMock(spec=FlightMetadataProvider)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            provider = client(http, metadata=metadata)
            for _ in range(2):
                result = await provider.search_flights(request=request())
                assert result.status.value == "no_offers"
            assert not http.is_closed
        assert len(calls) == 3
        search = calls[1]
        assert search.method == "POST"
        assert (
            str(search.url)
            == "https://api.pp.travelport.net/11/air/catalog/search/catalogproductofferings"
        )
        assert search.headers["Authorization"] == "Bearer test-token"
        assert search.headers["TVP-PCC-Core"] == "TEST_1G"
        assert search.headers["TraceId"] != calls[2].headers["TraceId"]
        assert json.loads(search.content)["CatalogProductOfferingsRequest"][
            "contentSourceList"
        ] == ["NDC"]
        assert all(r.is_closed for r in responses)
        metadata.resolve.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("second_status", [200, 401])
def test_401_recovers_only_once_and_closes_both_responses(second_status):
    async def run():
        calls, responses = [], []

        def handle(req):
            calls.append(req)
            response = httpx.Response(
                401 if len(calls) == 1 else second_status, json=EMPTY_RESULT
            )
            responses.append(response)
            return response

        tokens = [SecretStr("first"), SecretStr("second")]
        auth = MagicMock(spec=TravelportAuthClient)
        auth.get_access_token = AsyncMock(side_effect=tokens)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            provider = client(http, auth=auth)
            if second_status == 401:
                with pytest.raises(ProviderConfigurationError):
                    await provider.search_flights(request=request())
            else:
                assert (
                    await provider.search_flights(request=request())
                ).status.value == "no_offers"
        assert len(calls) == 2
        assert calls[0].headers["TraceId"] == calls[1].headers["TraceId"]
        assert calls[1].headers["Authorization"] == "Bearer second"
        assert auth.invalidate.call_args_list[0].kwargs["rejected_token"] is tokens[0]
        assert auth.invalidate.call_count == (2 if second_status == 401 else 1)
        assert all(r.is_closed for r in responses)

    asyncio.run(run())


@pytest.mark.parametrize("status", [403, 429, 500, 302])
def test_other_http_errors_do_not_retry_or_follow_redirects(status):
    async def run():
        calls, responses = [], []

        def handle(req):
            calls.append(req)
            response = httpx.Response(
                status,
                text="private-detail",
                headers={"Location": "https://other.test"},
            )
            responses.append(response)
            return response

        auth = MagicMock(spec=TravelportAuthClient)
        auth.get_access_token = AsyncMock(return_value=SecretStr("test-token"))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), follow_redirects=True
        ) as http:
            with pytest.raises(
                ProviderConfigurationError
                if status == 403
                else ProviderUnavailableError
            ) as caught:
                await client(http, auth=auth).search_flights(request=request())
        assert "private-detail" not in str(caught.value)
        assert len(calls) == 1
        assert responses[0].is_closed
        auth.invalidate.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["group", "return"])
def test_unsupported_searches_do_not_authenticate_or_call_http(mode):
    async def run():
        auth = MagicMock(spec=TravelportAuthClient)
        auth.get_access_token = AsyncMock()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP call"))
        ) as http:
            provider = client(http, auth=auth)
            if mode == "group":
                result = await provider.search_flights(request=request(adults=10))
                assert result.status.value == "group_booking_required"
            else:
                with pytest.raises(ProviderUnavailableError, match="return searches"):
                    await provider.search_flights(
                        request=request(return_date="2027-11-10")
                    )
        auth.get_access_token.assert_not_awaited()

    asyncio.run(run())


def test_overall_deadline_includes_authentication():
    async def run():
        async def authenticate():
            await asyncio.Event().wait()

        auth = MagicMock(spec=TravelportAuthClient)
        auth.get_access_token = AsyncMock(side_effect=authenticate)
        async with httpx.AsyncClient() as http:
            provider = client(
                http, config=settings(provider_timeout_seconds=0.01), auth=auth
            )
            with pytest.raises(ProviderUnavailableError, match="unavailable"):
                await asyncio.wait_for(
                    provider.search_flights(request=request()), timeout=1
                )

    asyncio.run(run())
