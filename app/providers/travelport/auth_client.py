import asyncio
from collections.abc import Callable
from time import monotonic

import httpx
from pydantic import SecretStr

from app.common.exceptions import ProviderConfigurationError, ProviderUnavailableError
from app.config import Settings
from app.providers.travelport.auth_schemas import TravelportTokenResponse

TOKEN_REFRESH_MARGIN_SECONDS = 60.0


class TravelportAuthClient:
    """Share one cached token across adapters using the same credentials."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        credentials = {
            "username": settings.travelport_username,
            "password": settings.travelport_password,
            "client_id": settings.travelport_client_id,
            "client_secret": settings.travelport_client_secret,
        }
        self._credentials: dict[str, SecretStr] = {}

        for name, value in credentials.items():
            if value is None or not value.get_secret_value().strip():
                raise ProviderConfigurationError(f"Travelport {name} is required")

            self._credentials[name] = value

        self._http_client = http_client
        self._auth_url = settings.travelport_auth_url
        self._timeout_seconds = settings.provider_timeout_seconds
        self._clock = clock

        self._lock = asyncio.Lock()
        self._token: SecretStr | None = None
        self._refresh_at = 0.0

    async def get_access_token(self) -> SecretStr:
        """Reuse a valid token or serialize token acquisition."""

        async with self._lock:
            if self._token is not None and self._clock() < self._refresh_at:
                return self._token

            started_at = self._clock()
            token_response = await self._request_token()
            refresh_margin = min(
                TOKEN_REFRESH_MARGIN_SECONDS, token_response.expires_in * 0.1
            )
            refresh_at = started_at + token_response.expires_in - refresh_margin

            if self._clock() >= refresh_at:
                raise ProviderUnavailableError(
                    "Travelport returned a token with insufficient lifetime"
                )

            self._token = token_response.access_token
            self._refresh_at = refresh_at
            return self._token

    def invalidate(self, *, rejected_token: SecretStr) -> None:
        """Invalidate only the cached token used by a rejected request."""

        if self._token is rejected_token:
            self._token = None
            self._refresh_at = 0.0

    async def _request_token(self) -> TravelportTokenResponse:
        """Acquire and validate a token without exposing credentials."""

        payload = {
            "grant_type": "password",
            **{
                name: value.get_secret_value()
                for name, value in self._credentials.items()
            },
        }
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._http_client.post(
                    self._auth_url,
                    data=payload,
                    headers={"Accept": "application/json"},
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                )
                response.raise_for_status()
                return TravelportTokenResponse.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {400, 401, 403}:
                raise ProviderConfigurationError(
                    "Travelport authentication was rejected"
                ) from None

            raise ProviderUnavailableError(
                "Travelport authentication is unavailable"
            ) from None

        except (httpx.HTTPError, TimeoutError):
            raise ProviderUnavailableError(
                "Travelport authentication is unavailable"
            ) from None

        except (ValueError, TypeError):
            raise ProviderUnavailableError(
                "Travelport returned an invalid authentication response"
            ) from None
