"""Opt-in end-to-end planning against a private disposable PostgreSQL cluster."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import AIMessage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Conversation, Itinerary, User
from app.database.repositories.planning import PlanningRepository
from app.domain.planning import PlanningState
from app.graph.planning_builder import build_planning_graph
from app.services.conversation_planning_service import ConversationPlanningService
from app.services.conversation_processing_service import ConversationProcessingService
from app.services.conversation_service import ConversationService
from app.services.itinerary_service import ItineraryService
from app.services.planning_research_service import PlanningResearchService
from app.services.travel_response_service import TravelResponseService


def test_planning_atomic_persistence_retry_and_stale_lease(
    postgres_url, migrate_database
):
    asyncio.run(exercise_planning(postgres_url, migrate_database))


async def exercise_planning(url, migrate):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            user_id = uuid4()
            session.add(
                User(id=user_id, email="planner@example.com", password_hash="test-hash")
            )
            await session.commit()
            gateway = AsyncMock()
            generated = {
                "summary": "Japan draft",
                "items": [
                    {
                        "day_number": n,
                        "item_type": "activity",
                        "title": "Explore at your own pace",
                    }
                    for n in range(1, 6)
                ],
            }
            gateway.generate.side_effect = [
                AIMessage(content=json.dumps(value))
                for value in [
                    {
                        "intent": "plan",
                        "updates": {"destination": "Japan", "duration_days": 5},
                    },
                    {
                        "intent": "plan",
                        "updates": {
                            "start_date": "2099-11-07",
                            "adults": 2,
                            "minor_count": 0,
                            "needs_lodging": False,
                            "transport": "own_arrangements",
                            "budget_decision": "undecided",
                        },
                    },
                    generated,
                    {"intent": "revise", "updates": {"interests": ["food"]}},
                    generated,
                ]
            ]
            graph = build_planning_graph(
                model_gateway=gateway,
                research_service=PlanningResearchService(
                    client=AsyncMock(),
                    places_available=False,
                    hotels_available=False,
                    round_trip_flights_available=False,
                ),
            )
            processing = ConversationProcessingService(
                session=session, history_limit=20
            )
            service = TravelResponseService(
                processing_service=processing,
                itinerary_service=ItineraryService(session=session),
                planning_service=ConversationPlanningService(session=session),
                graph=graph,
                assistant_run_lease_seconds=120,
                travel_response_timeout_seconds=75,
                max_model_attempts=3,
            )
            conversations = ConversationService(session=session)
            first = await conversations.accept_request(
                user_id=user_id,
                client_message_id=uuid4(),
                conversation_id=None,
                message="Japan 5 days",
                locale="en",
            )
            conversation_id = first.conversation.id
            await service.generate_reply(user_id=user_id, accepted_request=first)
            payload = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            assert (
                PlanningState.model_validate(payload).requirements.destination
                == "Japan"
            )
            second = await conversations.accept_request(
                user_id=user_id,
                client_message_id=uuid4(),
                conversation_id=conversation_id,
                message="November 7 2099, two adults, no children, own transport, no hotel, budget undecided",
                locale="en",
            )
            result = await service.generate_reply(
                user_id=user_id, accepted_request=second
            )
            assert result.itinerary_id is not None and result.rich_content is not None
            cached = await service.generate_reply(
                user_id=user_id, accepted_request=second
            )
            assert cached.is_cached and cached.itinerary_id == result.itinerary_id
            assert gateway.generate.await_count == 3
            before = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            count = await session.scalar(select(func.count()).select_from(Itinerary))
            third = await conversations.accept_request(
                user_id=user_id,
                client_message_id=uuid4(),
                conversation_id=conversation_id,
                message="More food please",
                locale="en",
            )
            original_save = processing.save_reply
            processing.save_reply = AsyncMock(
                side_effect=RuntimeError("simulated reply write failure")
            )
            try:
                await service.generate_reply(user_id=user_id, accepted_request=third)
            except RuntimeError as error:
                assert str(error) == "simulated reply write failure"
            else:
                raise AssertionError("Expected transaction failure")
            finally:
                processing.save_reply = original_save
            assert (
                await session.scalar(select(func.count()).select_from(Itinerary))
                == count
            )
            after = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            assert after == before
            await session.commit()
        async with sessions() as first_session, sessions() as second_session:
            first_repo, second_repo = (
                PlanningRepository(first_session),
                PlanningRepository(second_session),
            )
            old_token, new_token = uuid4(), uuid4()
            assert (
                await first_repo.acquire(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    token=old_token,
                    seconds=120,
                )
                is not None
            )
            await first_session.commit()
            assert (
                await second_repo.acquire(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    token=new_token,
                    seconds=120,
                )
                is None
            )
            await second_session.commit()
            await first_session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    planning_lease_expires_at=func.clock_timestamp()
                    - timedelta(seconds=1)
                )
            )
            await first_session.commit()
            assert (
                await second_repo.acquire(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    token=new_token,
                    seconds=120,
                )
                is not None
            )
            await second_session.commit()
            try:
                await first_repo.stage(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    token=old_token,
                    state={},
                    trip_id=None,
                )
            except RuntimeError:
                await first_session.rollback()
            else:
                raise AssertionError("Expired lease overwrote a newer owner")
    finally:
        await engine.dispose()
