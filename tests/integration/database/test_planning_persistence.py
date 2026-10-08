"""Opt-in end-to-end planning against a private disposable PostgreSQL cluster."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.schemas.itineraries import ItineraryItemResponse
from app.database.models import Conversation, Itinerary, ItineraryItem, Message, User
from app.database.repositories.planning import PlanningRepository
from app.domain.planning import PlanningState
from app.graph.planning_builder import build_planning_graph
from app.graph.subgraphs.model_gateway import ModelGatewayError
from app.providers.weather.schemas import WeatherForecast
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
                        "starts_at": f"2099-11-{n + 6:02}T10:00:00+09:00",
                        "ends_at": f"2099-11-{n + 6:02}T15:00:00+09:00",
                    }
                    for n in range(1, 6)
                ],
            }
            gateway.generate.side_effect = [
                AIMessage(
                    content=json.dumps(
                        {**value, "changed_fields": list(value["updates"])}
                        if "intent" in value
                        else value
                    )
                )
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
            weather_client = AsyncMock()
            weather_client.get_weather_forecast.return_value = (
                WeatherForecast.model_validate(
                    {
                        "location": "Tokyo",
                        "time_zone": "Asia/Tokyo",
                        "days": [
                            {
                                "date": "2099-11-07",
                                "condition": "Rain",
                                "max_temperature_c": 20,
                                "min_temperature_c": 10,
                                "total_precipitation_mm": 2,
                                "hours": [
                                    {
                                        "local_time": "2099-11-07T14:00:00+09:00",
                                        "condition": "Rain",
                                        "temperature_c": 18,
                                        "chance_of_rain_percent": 90,
                                    }
                                ],
                            }
                        ],
                    }
                )
            )
            graph = build_planning_graph(
                model_gateway=gateway,
                research_service=PlanningResearchService(
                    client=weather_client,
                    places_available=False,
                    hotels_available=False,
                    round_trip_flights_available=False,
                    weather_forecasts_available=True,
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
            assert cached.rich_content == result.rich_content
            day_one = cached.rich_content.sections[-1].days[0].activities
            assert len(day_one) == 2
            assert day_one[0].starts_at.isoformat() == "2099-11-07T10:00:00+09:00"
            assert day_one[0].ends_at.isoformat() == "2099-11-07T15:00:00+09:00"
            assert "90%" in day_one[1].description
            persisted_items = list(
                await session.scalars(
                    select(ItineraryItem)
                    .where(ItineraryItem.itinerary_id == result.itinerary_id)
                    .order_by(ItineraryItem.day_number, ItineraryItem.position)
                )
            )
            restored = [
                ItineraryItemResponse.model_validate(item) for item in persisted_items
            ]
            assert restored[0].starts_at.isoformat() == "2099-11-07T10:00:00+09:00"
            assert restored[0].ends_at.isoformat() == "2099-11-07T15:00:00+09:00"
            assert "90%" in restored[1].description
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
            third_message_id = third.user_message.id
            third_client_message_id = third.user_message.client_message_id
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
            previous = PlanningState.model_validate(before)
            persisted = PlanningState.model_validate(after)
            assert persisted.requirements.interests == ("food",)
            assert persisted.requirements_message_id == third_message_id
            assert persisted.revision == previous.revision + 1
            assert persisted.phase == "ready"
            assert persisted.itinerary == previous.itinerary

            retry_request = await conversations.accept_request(
                user_id=user_id,
                client_message_id=third_client_message_id,
                conversation_id=conversation_id,
                message="More food please",
                locale="en",
            )
            calls_before_retry = gateway.generate.await_count
            gateway.generate.side_effect = [AIMessage(content=json.dumps(generated))]
            retried = await service.generate_reply(
                user_id=user_id, accepted_request=retry_request
            )
            assert retried.itinerary_id is not None
            assert retried.is_cached is False
            assert gateway.generate.await_count == calls_before_retry + 1

            restored_payload = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            restored = PlanningState.model_validate(restored_payload)
            assert restored.phase == "generated"
            assert restored.requirements.interests == ("food",)
            assert restored.requirements_message_id == third_message_id
            assert restored.revision == persisted.revision

            cached_retry = await service.generate_reply(
                user_id=user_id, accepted_request=retry_request
            )
            assert cached_retry.is_cached
            assert cached_retry.itinerary_id == retried.itinerary_id
            assert gateway.generate.await_count == calls_before_retry + 1
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


def test_requirements_survive_synthesis_failure_and_fresh_session(
    postgres_url,
    migrate_database,
):
    asyncio.run(exercise_fresh_session_recovery(postgres_url, migrate_database))


def create_recovery_service(session, gateway):
    graph = build_planning_graph(
        model_gateway=gateway,
        research_service=PlanningResearchService(
            client=AsyncMock(),
            places_available=False,
            hotels_available=False,
            round_trip_flights_available=False,
        ),
    )

    return TravelResponseService(
        processing_service=ConversationProcessingService(
            session=session,
            history_limit=20,
        ),
        itinerary_service=ItineraryService(session=session),
        planning_service=ConversationPlanningService(session=session),
        graph=graph,
        assistant_run_lease_seconds=120,
        travel_response_timeout_seconds=75,
        max_model_attempts=3,
    )


async def exercise_fresh_session_recovery(url, migrate):
    engine = create_async_engine(url)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate)

        sessions = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid4()
        client_message_id = uuid4()
        message = "Plan a five-day Japan trip."

        extracted_requirements = {
            "destination": "Japan",
            "start_date": "2099-11-07",
            "duration_days": 5,
            "adults": 2,
            "minor_count": 0,
            "needs_lodging": False,
            "transport": "own_arrangements",
            "budget_decision": "undecided",
        }

        # First attempt: extraction succeeds, synthesis fails.
        async with sessions() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"recovery-{user_id}@example.com",
                    password_hash="test-hash",
                )
            )
            await session.commit()

            accepted = await ConversationService(session=session).accept_request(
                user_id=user_id,
                client_message_id=client_message_id,
                conversation_id=None,
                message=message,
                locale="en",
            )

            # Keep scalar IDs before rollback expires ORM instances.
            conversation_id = accepted.conversation.id
            user_message_id = accepted.user_message.id

            gateway = AsyncMock()
            gateway.generate.side_effect = [
                AIMessage(
                    content=json.dumps(
                        {
                            "intent": "plan",
                            "updates": extracted_requirements,
                            "changed_fields": list(extracted_requirements),
                        }
                    )
                ),
                ModelGatewayError("simulated synthesis failure"),
            ]

            service = create_recovery_service(session, gateway)

            with pytest.raises(ModelGatewayError):
                await service.generate_reply(
                    user_id=user_id,
                    accepted_request=accepted,
                )

            assert gateway.generate.await_count == 2

        # Fresh session: all state must come from PostgreSQL.
        async with sessions() as session:
            payload = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            persisted = PlanningState.model_validate(payload)

            assert persisted.phase == "ready"
            assert persisted.requirements.destination == "Japan"
            assert persisted.requirements.duration_days == 5
            assert persisted.requirements.adults == 2
            assert persisted.requirements_message_id == user_message_id

            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.reply_to_message_id == user_message_id)
                )
                == 0
            )

            assert (
                await session.scalar(select(func.count()).select_from(Itinerary)) == 0
            )

            retry_request = await ConversationService(session=session).accept_request(
                user_id=user_id,
                client_message_id=client_message_id,
                conversation_id=conversation_id,
                message=message,
                locale="en",
            )

            assert retry_request.is_duplicate
            assert retry_request.user_message.id == user_message_id

            generated = {
                "summary": "Japan draft",
                "items": [
                    {
                        "day_number": day,
                        "item_type": "activity",
                        "title": "Explore at your own pace",
                    }
                    for day in range(1, 6)
                ],
            }

            recovery_gateway = AsyncMock()

            # Exactly one response is available: synthesis.
            # Unexpected re-extraction therefore fails the test.
            recovery_gateway.generate.side_effect = [
                AIMessage(content=json.dumps(generated))
            ]
            recovery_service = create_recovery_service(
                session,
                recovery_gateway,
            )

            result = await recovery_service.generate_reply(
                user_id=user_id,
                accepted_request=retry_request,
            )

            assert result.is_cached is False
            assert result.itinerary_id is not None
            assert result.rich_content is not None
            recovery_gateway.generate.assert_awaited_once()

            cached = await recovery_service.generate_reply(
                user_id=user_id,
                accepted_request=retry_request,
            )

            assert cached.is_cached
            assert cached.itinerary_id == result.itinerary_id
            recovery_gateway.generate.assert_awaited_once()

        # A third session confirms the final writes were committed.
        async with sessions() as session:
            payload = await session.scalar(
                select(Conversation.planning_state).where(
                    Conversation.id == conversation_id
                )
            )
            restored = PlanningState.model_validate(payload)

            assert restored.phase == "generated"
            assert restored.requirements == persisted.requirements
            assert restored.revision == persisted.revision
            assert restored.requirements_message_id == user_message_id

            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.reply_to_message_id == user_message_id)
                )
                == 1
            )

            assert (
                await session.scalar(select(func.count()).select_from(Itinerary)) == 1
            )

    finally:
        await engine.dispose()
