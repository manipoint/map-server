import argparse
import asyncio
import logging
from datetime import date

import httpx
from langchain_core.messages import HumanMessage

from app.config import get_settings
from app.graph.builder import build_travel_graph
from app.graph.subgraphs.model_gateway import build_model_gateway
from app.graph.tools import create_hotel_search_tool
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.observability.logging import configure_logging
from app.providers.hotels.duffel_client import DuffelHotelClient
from app.providers.hotels.schemas import HotelSearchInput
from app.providers.locations.weatherapi_client import WeatherApiLocationClient
from app.providers.weather.client import WeatherApiClient
from app.services.hotel_search_service import HotelSearchService

logger = logging.getLogger(__name__)


async def check_hotel_graph(
    destination: str,
    check_in_date: date,
    check_out_date: date,
) -> None:
    """Run one hotel request through MCP and LangGraph."""

    request = HotelSearchInput(
        destination=destination,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        max_results=3,
    )
    settings = get_settings()
    configure_logging(settings.log_level)

    async with httpx.AsyncClient() as http_client:
        weather_provider = WeatherApiClient(
            http_client=http_client,
            settings=settings,
        )
        location_provider = WeatherApiLocationClient(
            http_client=http_client,
            settings=settings,
        )
        hotel_provider = DuffelHotelClient(
            http_client=http_client,
            settings=settings,
        )
        hotel_service = HotelSearchService(
            location_provider=location_provider,
            hotel_provider=hotel_provider,
            radius_km=settings.duffel_stays_radius_km,
        )

        mcp_server = create_mcp_server(
            weather_provider=weather_provider,
            hotel_search_service=hotel_service,
        )
        mcp_client = TravelMcpClient(mcp_server=mcp_server)
        hotel_tool = create_hotel_search_tool(mcp_client=mcp_client)

        model_gateway = build_model_gateway(
            settings=settings,
            tools=[hotel_tool],
        )
        graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=[hotel_tool],
            max_tool_rounds=settings.max_tool_rounds,
        )

        result = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            f"Find up to 3 hotels in {request.destination} "
                            f"from {request.check_in_date.isoformat()} to "
                            f"{request.check_out_date.isoformat()} for 1 adult. "
                            "Summarize hotel names, total prices, currency, "
                            "ratings, and cancellation information when available."
                        )
                    )
                ],
                "locale": "en-PK",
            }
        )

    logger.info(
        "Hotel graph check completed",
        extra={
            "destination": request.destination,
            "check_in_date": request.check_in_date.isoformat(),
            "check_out_date": request.check_out_date.isoformat(),
            "assistant_response": result["assistant_response"],
        },
    )


def parse_arguments() -> argparse.Namespace:
    """Parse hotel-search arguments."""

    parser = argparse.ArgumentParser(
        description="Check the live hotel-search LangGraph flow."
    )
    parser.add_argument("destination")
    parser.add_argument("check_in_date", type=date.fromisoformat)
    parser.add_argument("check_out_date", type=date.fromisoformat)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(
        check_hotel_graph(
            arguments.destination,
            arguments.check_in_date,
            arguments.check_out_date,
        )
    )
