"""Opt-in two-turn deal selection against an isolated PostgreSQL database."""

import asyncio
import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Conversation, User
from app.domain.planning import PlanningState
from app.graph.planning_builder import build_planning_graph
from app.providers.airports.schemas import AirportResolution
from app.providers.serpapi.deals_client import FlightDeal
from app.services.conversation_planning_service import ConversationPlanningService
from app.services.conversation_processing_service import ConversationProcessingService
from app.services.conversation_service import ConversationService
from app.services.deal_discovery_service import DealChoice, DealDiscoveryResult
from app.services.itinerary_service import ItineraryService
from app.services.standalone_search_service import StandaloneSearchService
from app.services.travel_response_service import TravelResponseService


def test_standalone_deal_selection_persists_across_requests(
    postgres_url, migrate_database
):
    asyncio.run(_exercise_deal_selection(postgres_url, migrate_database))


async def _exercise_deal_selection(url, migrate):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid4()
        async with sessions() as session:
            session.add(
                User(
                    id=user_id,
                    email="deal-planner@example.com",
                    password_hash="test-hash",
                )
            )
            await session.commit()

        deal = DealChoice(
            option_id="deal-1",
            deal=FlightDeal(
                origin="LHE",
                destination="DXB",
                start_date=date(2099, 12, 10),
                end_date=date(2099, 12, 14),
                price=Decimal("220"),
                currency="USD",
                airline="Example Air",
                flight_link="https://www.google.com/travel/flights",
            ),
        )
        deals = AsyncMock()
        deals.discover.return_value = DealDiscoveryResult(
            status="deals_available",
            origin=AirportResolution(
                status="resolved", query="Lahore", iata_code="LHE"
            ),
            destination=AirportResolution(
                status="resolved", query="Dubai", iata_code="DXB"
            ),
            options=(deal,),
        )
        travel_client = AsyncMock()
        standalone = StandaloneSearchService(
            client=travel_client,
            enabled=frozenset({"flight_deals"}),
            deal_discovery_service=deals,
        )
        gateway = AsyncMock()
        gateway.generate.return_value = AIMessage(
            content=json.dumps(
                {
                    "intent": "search",
                    "updates": {},
                    "changed_fields": [],
                    "search": {
                        "kind": "flight_deals",
                        "arguments": {
                            "origin": "Lahore",
                            "destination": "Dubai",
                            "window_start": "2099-12-01",
                            "window_end": "2099-12-31",
                        },
                    },
                }
            )
        )
        research = AsyncMock()
        research.cache_key = Mock(return_value="deal-research")
        graph = build_planning_graph(
            model_gateway=gateway,
            research_service=research,
            standalone_service=standalone,
        )

        def make_service(session):
            return TravelResponseService(
                processing_service=ConversationProcessingService(
                    session=session, history_limit=20
                ),
                itinerary_service=ItineraryService(session=session),
                planning_service=ConversationPlanningService(session=session),
                graph=graph,
                assistant_run_lease_seconds=120,
                travel_response_timeout_seconds=75,
                max_model_attempts=3,
            )

        async with sessions() as session:
            first = await ConversationService(session=session).accept_request(
                user_id=user_id,
                client_message_id=uuid4(),
                conversation_id=None,
                message="Find Lahore to Dubai flight deals in December 2099",
                locale="en",
            )
            conversation_id = first.conversation.id
            reply = await make_service(session).generate_reply(
                user_id=user_id, accepted_request=first
            )
            assert reply.error_code is None
            stored = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            assert PlanningState.model_validate(
                stored
            ).pending_travel_selection.option_ids == ("deal-1",)

        async with sessions() as session:
            second = await ConversationService(session=session).accept_request(
                user_id=user_id,
                client_message_id=uuid4(),
                conversation_id=conversation_id,
                message="deal 1",
                locale="en",
            )
            reply = await make_service(session).generate_reply(
                user_id=user_id, accepted_request=second
            )
            assert reply.error_code is None
            stored = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            selected = PlanningState.model_validate(stored)
            assert selected.selected_deal["option_id"] == "deal-1"
            assert selected.requirements.origin == "Lahore"
            assert selected.requirements.destination == "Dubai"
            assert selected.requirements.start_date == date(2099, 12, 10)
            assert selected.requirements.end_date == date(2099, 12, 14)

        deals.discover.assert_awaited_once()
        gateway.generate.assert_awaited_once()
        travel_client.search_flights.assert_not_awaited()
        research.research.assert_not_awaited()
    finally:
        await engine.dispose()
