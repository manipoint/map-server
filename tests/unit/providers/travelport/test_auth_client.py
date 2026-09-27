"""Deterministic Travelport OAuth and cache regression tests."""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import ProviderConfigurationError, ProviderUnavailableError
from app.config import Settings
from app.providers.travelport.auth_client import TravelportAuthClient


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
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "field", ["username", "password", "client_id", "client_secret"]
)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_credentials_fail_before_network(field, value):
    async def run():
        async with httpx.AsyncClient() as http:
            with pytest.raises(ProviderConfigurationError):
                TravelportAuthClient(
                    http_client=http,
                    settings=settings(
                        **{
                            f"travelport_{field}": None
                            if value is None
                            else SecretStr(value)
                        }
                    ),
                )

    asyncio.run(run())


def test_concurrent_requests_share_token_and_send_form_credentials():
    async def run():
        calls = []

        async def handle(request):
            calls.append(request)
            await asyncio.sleep(0)
            return httpx.Response(200, json={"access_token": "test-token"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = TravelportAuthClient(http_client=http, settings=settings())
            tokens = await asyncio.gather(
                *(client.get_access_token() for _ in range(20))
            )
            assert all(token is tokens[0] for token in tokens)
            assert tokens[0].get_secret_value() == "test-token"
            assert await client.get_access_token() is tokens[0]
            assert not http.is_closed
        assert len(calls) == 1
        request = calls[0]
        assert str(request.url) == "https://auth.pp.travelport.net/oauth/token"
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert parse_qs(request.content.decode()) == {
            "grant_type": ["password"],
            "username": ["test-user"],
            "password": ["test-password"],
            "client_id": ["test-client"],
            "client_secret": ["test-secret"],
        }

    asyncio.run(run())


def test_expiry_and_stale_invalidation():
    async def run():
        now = [0.0]
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(
                200, json={"access_token": f"token-{len(calls)}", "expires_in": 100}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = TravelportAuthClient(
                http_client=http, settings=settings(), clock=lambda: now[0]
            )
            first = await client.get_access_token()
            now[0] = 89.0
            assert await client.get_access_token() is first
            now[0] = 90.0
            second = await client.get_access_token()
            assert second is not first
            client.invalidate(rejected_token=first)
            assert await client.get_access_token() is second
            client.invalidate(rejected_token=second)
            assert await client.get_access_token() is not second
            assert len(calls) == 3

    asyncio.run(run())


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 302])
def test_http_failures_are_sanitized_and_redirects_not_followed(status):
    async def run():
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(
                status,
                text="private-provider-detail",
                headers={"Location": "https://other.test"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), follow_redirects=True
        ) as http:
            client = TravelportAuthClient(http_client=http, settings=settings())
            expected = (
                ProviderConfigurationError
                if status in {400, 401, 403}
                else ProviderUnavailableError
            )
            with pytest.raises(expected) as caught:
                await client.get_access_token()
            assert "private-provider-detail" not in str(caught.value)
            assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    ["not-json", "{}", '{"access_token":""}', '{"access_token":"x","expires_in":0}'],
)
def test_invalid_response_is_rejected(body):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        ) as http:
            client = TravelportAuthClient(http_client=http, settings=settings())
            with pytest.raises(ProviderUnavailableError):
                await client.get_access_token()

    asyncio.run(run())


def test_cancelled_authentication_releases_lock_for_next_request():
    async def run():
        started = asyncio.Event()
        calls = 0

        async def handle(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json={"access_token": "recovered"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = TravelportAuthClient(http_client=http, settings=settings())
            pending = asyncio.create_task(client.get_access_token())
            await asyncio.wait_for(started.wait(), timeout=1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            token = await asyncio.wait_for(client.get_access_token(), timeout=1)
            assert token.get_secret_value() == "recovered"
            assert calls == 2

    asyncio.run(run())


def test_overall_authentication_deadline_is_enforced():
    async def run():
        async def handle(request):
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = TravelportAuthClient(
                http_client=http, settings=settings(provider_timeout_seconds=0.01)
            )
            with pytest.raises(ProviderUnavailableError, match="unavailable"):
                await asyncio.wait_for(client.get_access_token(), timeout=1)

    asyncio.run(run())


def test_token_that_expired_during_authentication_is_not_cached():
    async def run():
        now = [0.0]
        calls = 0

        def handle(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                now[0] = 100.0
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": 100}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = TravelportAuthClient(
                http_client=http, settings=settings(), clock=lambda: now[0]
            )
            with pytest.raises(ProviderUnavailableError, match="insufficient lifetime"):
                await client.get_access_token()
            await client.get_access_token()
            assert calls == 2

    asyncio.run(run())


def test_network_failure_does_not_poison_lock_or_cache():
    async def run():
        calls = []

        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("private-detail", request=request)
            return httpx.Response(200, json={"access_token": "recovered"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = TravelportAuthClient(http_client=http, settings=settings())
            with pytest.raises(ProviderUnavailableError):
                await client.get_access_token()
            assert (await client.get_access_token()).get_secret_value() == "recovered"

    asyncio.run(run())
