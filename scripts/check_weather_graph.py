"""Run one live weather request through MCP and LangGraph."""

import argparse
import asyncio

import httpx
from langchain_core.messages import HumanMessage

from app.config import get_settings
from app.graph.builder import build_travel_graph
from app.graph.subgraphs.model_gateway import build_model_gateway
from app.graph.tools import create_current_weather_tool
from app.mcp.client import TravelMcpClient
from app.mcp.schemas.weather import CurrentWeatherInput
from app.mcp.server import create_mcp_server
from app.providers.weather.client import WeatherApiClient


async def check_weather_graph(city: str) -> None:
    """Run one city through the complete weather graph."""

    request = CurrentWeatherInput(city=city)
    settings = get_settings()

    async with httpx.AsyncClient() as http_client:
        weather_provider = WeatherApiClient(
            http_client=http_client,
            settings=settings,
        )
        mcp_server = create_mcp_server(
            weather_provider=weather_provider,
        )
        mcp_client = TravelMcpClient(mcp_server=mcp_server)
        weather_tool = create_current_weather_tool(
            mcp_client=mcp_client,
        )
        model_gateway = build_model_gateway(
            settings=settings,
            tools=[weather_tool],
        )
        graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=[weather_tool],
            max_tool_rounds=settings.max_tool_rounds,
        )

        result = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(f"What is the current weather in {request.city}?")
                    )
                ],
                "locale": "en-PK",
            }
        )

        print(result["assistant_response"])


def parse_arguments() -> argparse.Namespace:
    """Parse the city supplied from the command line."""

    parser = argparse.ArgumentParser(
        description="Check the live weather LangGraph flow."
    )
    parser.add_argument("city", help="City whose current weather is required")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(check_weather_graph(arguments.city))
