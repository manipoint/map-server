"""Airport and metropolitan code resolution use cases."""

from pydantic import TypeAdapter, ValidationError

from app.domain.value_objects import IataCode
from app.providers.airports.client import AirportProvider
from app.providers.airports.schemas import (
    AirportResolution,
    AirportSearchInput,
)

_IATA_CODE_ADAPTER = TypeAdapter(IataCode)


class AirportResolutionService:
    """Resolve direct IATA codes or return bounded provider choices."""

    def __init__(self, *, airport_provider: AirportProvider) -> None:
        self.airport_provider = airport_provider

    async def resolve_airport(
        self,
        *,
        request: AirportSearchInput,
    ) -> AirportResolution:
        """Resolve one airport query without guessing between alternatives."""

        direct_iata_code = self._parse_direct_iata_code(request.query)
        if direct_iata_code is not None:
            return AirportResolution(
                status="resolved",
                query=request.query,
                iata_code=direct_iata_code,
            )

        search_result = await self.airport_provider.search_airports(request=request)

        if not search_result.options:
            return AirportResolution(
                status="not_found",
                query=request.query,
            )

        if len(search_result.options) == 1:
            return AirportResolution(
                status="resolved",
                query=request.query,
                iata_code=search_result.options[0].iata_code,
            )

        return AirportResolution(
            status="selection_required",
            query=request.query,
            options=search_result.options,
        )

    @staticmethod
    def _parse_direct_iata_code(query: str) -> str | None:
        """Return a normalized direct IATA code, if the query is one."""

        try:
            return _IATA_CODE_ADAPTER.validate_python(query)
        except ValidationError:
            return None
