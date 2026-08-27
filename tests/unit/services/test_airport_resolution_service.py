"""Tests for deterministic airport-code resolution."""

import asyncio

import pytest

from app.common.exceptions import ProviderUnavailableError
from app.providers.airports.schemas import (
    AirportOption,
    AirportSearchInput,
    AirportSearchResult,
)
from app.services.airport_resolution_service import AirportResolutionService


def airport_option(*, iata_code: str, name: str) -> AirportOption:
    """Build one normalized airport option."""

    return AirportOption(
        provider_location_id=f"apt_{iata_code.lower()}",
        iata_code=iata_code,
        location_type="airport",
        name=name,
        city_name="London",
        country_name="United Kingdom",
        country_code="GB",
    )


class FakeAirportProvider:
    """Return one configured search result while recording requests."""

    def __init__(
        self,
        options: list[AirportOption] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.options = options or []
        self.error = error
        self.requests: list[AirportSearchInput] = []

    async def search_airports(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportSearchResult:
        """Record the lookup and return or raise the configured outcome."""

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return AirportSearchResult(query=request.query, options=self.options)


def test_direct_iata_code_resolves_without_provider_cost() -> None:
    """A supplied code should skip the external airport provider entirely."""

    async def exercise() -> None:
        provider = FakeAirportProvider(error=AssertionError("unexpected lookup"))
        service = AirportResolutionService(airport_provider=provider)

        result = await service.resolve_airport(
            request=AirportSearchInput(query=" lhr ")
        )

        assert result.status == "resolved"
        assert result.iata_code == "LHR"
        assert result.options == []
        assert provider.requests == []

    asyncio.run(exercise())


def test_single_provider_match_resolves_automatically() -> None:
    """One unambiguous city result should produce its verified code."""

    async def exercise() -> None:
        provider = FakeAirportProvider(
            options=[airport_option(iata_code="LHR", name="Heathrow Airport")]
        )
        service = AirportResolutionService(airport_provider=provider)

        result = await service.resolve_airport(
            request=AirportSearchInput(query="Heathrow", max_results=3)
        )

        assert result.status == "resolved"
        assert result.iata_code == "LHR"
        assert provider.requests[0].max_results == 3

    asyncio.run(exercise())


def test_ambiguous_city_requires_user_selection() -> None:
    """Multiple airports must be returned as choices instead of guessed."""

    async def exercise() -> None:
        options = [
            airport_option(iata_code="LHR", name="Heathrow Airport"),
            airport_option(iata_code="LGW", name="Gatwick Airport"),
        ]
        service = AirportResolutionService(
            airport_provider=FakeAirportProvider(options=options)
        )

        result = await service.resolve_airport(
            request=AirportSearchInput(query="London")
        )

        assert result.status == "selection_required"
        assert result.iata_code is None
        assert result.options == options

    asyncio.run(exercise())


def test_no_provider_match_returns_not_found() -> None:
    """Unknown places should become a safe structured outcome."""

    async def exercise() -> None:
        service = AirportResolutionService(airport_provider=FakeAirportProvider())

        result = await service.resolve_airport(
            request=AirportSearchInput(query="Unknown place")
        )

        assert result.status == "not_found"
        assert result.iata_code is None
        assert result.options == []

    asyncio.run(exercise())


def test_provider_failure_propagates_for_boundary_sanitization() -> None:
    """The service should not hide the provider's already-safe exception."""

    async def exercise() -> None:
        service = AirportResolutionService(
            airport_provider=FakeAirportProvider(
                error=ProviderUnavailableError("Airport search unavailable")
            )
        )

        with pytest.raises(ProviderUnavailableError, match="unavailable"):
            await service.resolve_airport(request=AirportSearchInput(query="London"))

    asyncio.run(exercise())
