"""Shared HTTP helpers for Duffel product adapters."""

from pydantic import SecretStr


def build_duffel_headers(
    *,
    api_key: SecretStr,
    api_version: str,
) -> dict[str, str]:
    """Build authentication and content headers for a Duffel request."""

    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Duffel-Version": api_version,
        "Authorization": f"Bearer {api_key.get_secret_value()}",
    }


def build_duffel_url(*, base_url: str, path: str) -> str:
    """Join one configured Duffel base URL and endpoint path."""

    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"
