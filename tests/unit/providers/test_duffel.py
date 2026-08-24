"""Tests for shared Duffel HTTP helpers."""

from pydantic import SecretStr

from app.providers.duffel import build_duffel_headers, build_duffel_url


def test_build_duffel_headers_returns_required_values() -> None:
    """All Duffel product clients should send one consistent header set."""

    headers = build_duffel_headers(
        api_key=SecretStr("test-duffel-key"),
        api_version="v2",
    )

    assert headers == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Duffel-Version": "v2",
        "Authorization": "Bearer test-duffel-key",
    }


def test_build_duffel_url_normalizes_boundary_slashes() -> None:
    """Base URLs and endpoint paths should join with exactly one slash."""

    assert (
        build_duffel_url(
            base_url="https://api.duffel.com/",
            path="/stays/search",
        )
        == "https://api.duffel.com/stays/search"
    )
