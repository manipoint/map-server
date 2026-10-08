"""Bounded SerpApi transport shared by travel-search adapters."""

import json
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.common.exceptions import ProviderConfigurationError, ProviderUnavailableError
from app.config import Settings


class SerpApiClient:
    """Execute bounded SerpApi requests without exposing credentials in errors."""

    def __init__(self, *, http_client: httpx.AsyncClient, settings: Settings) -> None:
        if settings.serpapi_api_key is None:
            raise ProviderConfigurationError("SerpApi credentials are not configured")
        endpoint = urlsplit(settings.serpapi_search_url)
        if endpoint.scheme != "https" or endpoint.hostname != "serpapi.com":
            raise ProviderConfigurationError("SerpApi search URL must use serpapi.com")

        self.http_client = http_client
        self.api_key: SecretStr = settings.serpapi_api_key
        self.endpoint = settings.serpapi_search_url
        self.timeout_seconds = settings.provider_timeout_seconds
        self.max_response_bytes = settings.serpapi_max_response_bytes

    async def search(self, *, engine: str, params: Mapping[str, object]) -> dict:
        """Return one successful JSON response within configured bounds."""
        query = {
            "engine": engine,
            "api_key": self.api_key.get_secret_value(),
            **{key: str(value) for key, value in params.items() if value is not None},
        }
        try:
            async with self.http_client.stream(
                "GET",
                self.endpoint,
                params=query,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise ProviderUnavailableError(
                            "SerpApi response exceeded the configured size limit"
                        )
                    chunks.append(chunk)

            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            metadata = payload.get("search_metadata")
            if isinstance(metadata, dict) and metadata.get("status") == "Error":
                raise ValueError("SerpApi search failed")
            if not isinstance(metadata, dict) or metadata.get("status") != "Success":
                raise ValueError("SerpApi response did not complete successfully")
            return payload
        except ProviderUnavailableError:
            raise
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise ProviderConfigurationError(
                    "SerpApi credentials were rejected"
                ) from error
            raise ProviderUnavailableError("SerpApi search is unavailable") from error
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProviderUnavailableError(
                "SerpApi returned an unavailable or invalid response"
            ) from error
