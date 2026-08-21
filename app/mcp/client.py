"""Graph-facing MCP client."""

from typing import Any, Protocol

from pydantic import ValidationError

from app.common.exceptions import ProviderUnavailableError
from app.mcp.schemas.weather import CurrentWeatherInput
from app.providers.weather.schemas import CurrentWeather


class McpToolServer(Protocol):
    """Minimum FastMCP interface required by graph-facing clients."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> Any:
        """Call a registered MCP tool."""


class TravelMcpClient:
    """Expose normalized travel data to LangGraph."""

    def __init__(self, mcp_server: McpToolServer) -> None:
        self.mcp_server = mcp_server

    async def get_current_weather(self, *, city: str) -> CurrentWeather:
        """Get validated normalized weather through the internal MCP server."""

        request = CurrentWeatherInput(city=city)
        try:
            result = await self.mcp_server.call_tool(
                "get_current_weather", {"city": request.city}
            )

        except Exception as error:
            raise ProviderUnavailableError(
                "Current-weather tool is unavailable"
            ) from error
        if result.is_error:
            raise ProviderUnavailableError("Current-weather tool failed")

        try:
            return CurrentWeather.model_validate(result.structured_content)
        except (AttributeError, TypeError, ValidationError) as error:
            raise ProviderUnavailableError(
                "Current-weather tool returned an invalid response"
            ) from error
