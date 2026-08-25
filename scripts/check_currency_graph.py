"""Run one live currency conversion through MCP and LangGraph."""

import argparse
import asyncio
import logging
from decimal import Decimal

import httpx
from langchain_core.messages import HumanMessage

from app.config import get_settings
from app.graph.builder import build_travel_graph
from app.graph.subgraphs.model_gateway import build_model_gateway
from app.graph.tools import create_currency_conversion_tool
from app.mcp.client import TravelMcpClient
from app.mcp.server import create_mcp_server
from app.observability.logging import configure_logging
from app.providers.currency.frankfurter_client import FrankfurterCurrencyClient
from app.providers.currency.schemas import CurrencyConversionInput
from app.providers.weather.client import WeatherApiClient

logger = logging.getLogger(__name__)


async def check_currency_graph(
    *,
    amount: Decimal,
    base_currency: str,
    quote_currency: str,
) -> None:
    """Run one validated conversion through the complete travel graph."""

    request = CurrencyConversionInput(
        amount=amount,
        base_currency=base_currency,
        quote_currency=quote_currency,
    )
    settings = get_settings()
    configure_logging(settings.log_level)

    async with httpx.AsyncClient() as http_client:
        weather_provider = WeatherApiClient(
            http_client=http_client,
            settings=settings,
        )
        currency_provider = FrankfurterCurrencyClient(
            http_client=http_client,
            settings=settings,
        )
        mcp_server = create_mcp_server(
            weather_provider=weather_provider,
            currency_provider=currency_provider,
        )
        mcp_client = TravelMcpClient(mcp_server=mcp_server)
        currency_tool = create_currency_conversion_tool(mcp_client=mcp_client)
        tools = [currency_tool]
        model_gateway = build_model_gateway(settings=settings, tools=tools)
        graph = build_travel_graph(
            model_gateway=model_gateway,
            tools=tools,
            max_tool_rounds=settings.max_tool_rounds,
        )
        result = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            f"Convert {request.amount} {request.base_currency} "
                            f"to {request.quote_currency}. Use convert_currency "
                            "exactly once. Report the converted amount, reference "
                            "rate, and rate date. State that it is not a payment quote."
                        )
                    )
                ],
                "locale": "en-PK",
            }
        )

    logger.info(
        "Currency graph check completed",
        extra={
            "amount": str(request.amount),
            "base_currency": request.base_currency,
            "quote_currency": request.quote_currency,
            "assistant_response": result["assistant_response"],
        },
    )


def parse_arguments() -> argparse.Namespace:
    """Parse one monetary amount and currency pair."""

    parser = argparse.ArgumentParser(
        description="Check one live currency-conversion LangGraph flow."
    )
    parser.add_argument("amount", type=Decimal, help="Non-negative amount to convert")
    parser.add_argument("base_currency", help="Three-letter source currency code")
    parser.add_argument("quote_currency", help="Three-letter target currency code")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    asyncio.run(
        check_currency_graph(
            amount=arguments.amount,
            base_currency=arguments.base_currency,
            quote_currency=arguments.quote_currency,
        )
    )
