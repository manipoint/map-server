"""Airport resolution using verified provider matches."""

from app.providers.airports.client import AirportProvider
from app.providers.airports.schemas import (
    AirportResolution,
    AirportSearchInput,
)


class AirportResolutionService:
    """Resolve verified matches or request an airport selection."""

    def __init__(
        self,
        *,
        airport_provider: AirportProvider,
    ) -> None:
        self.airport_provider = airport_provider

    async def resolve_airport(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportResolution:
        """Verify codes and names without bypassing airport lookup."""

        search_result = await self.airport_provider.search_airports(
            request=AirportSearchInput(
                query=request.query,
                max_results=5,
            ),
        )

        options = search_result.options

        if not options:
            return AirportResolution(
                status="not_found",
                query=request.query,
            )

        if len(options) == 1:
            return AirportResolution(
                status="resolved",
                query=request.query,
                iata_code=options[0].iata_code,
            )

        return AirportResolution(
            status="selection_required",
            query=request.query,
            options=options,
        )
