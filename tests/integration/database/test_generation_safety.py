"""Real PostgreSQL admission races, turn fencing and bounded history."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database.models import User
from app.database.models.generation_limits import GenerationLease, GenerationUsage
from app.database.repositories.messages import MessageRepository
from app.database.repositories.planning import PlanningRepository
from app.domain.enums import TravelResponseErrorCode
from app.domain.planning import PlanningState
from app.services.conversation_planning_service import (
    ConversationPlanningService,
    StalePlanningRequestError,
)
from app.services.conversation_service import ConversationService
from app.services.generation_admission import (
    GenerationAdmission,
    GenerationAdmissionError,
    GenerationAlreadyInProgressError,
)


def test_cross_worker_limits_and_turn_order(postgres_url, migrate_database):
    async def scenario():
        engine = create_async_engine(postgres_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(migrate_database)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            owner = uuid4()
            other = uuid4()
            async with sessions() as session:
                session.add_all(
                    [
                        User(id=owner, email="limit@example.com", password_hash="test"),
                        User(
                            id=other,
                            email="other-limit@example.com",
                            password_hash="test",
                        ),
                    ]
                )
                await session.commit()
            settings = Settings(
                _env_file=None,
                generation_user_concurrency=1,
                generation_global_concurrency=1,
                generation_daily_request_limit=2,
            )
            gate = GenerationAdmission(session_factory=sessions, settings=settings)
            all_attempted, release = asyncio.Event(), asyncio.Event()
            outcomes = []

            async def compete():
                message_id = uuid4()
                try:
                    async with gate.reserve(owner, message_id=message_id):
                        with pytest.raises(GenerationAlreadyInProgressError):
                            async with gate.reserve(owner, message_id=message_id):
                                pytest.fail("Duplicate reservation was accepted")
                        outcomes.append("accepted")
                        if len(outcomes) == 5:
                            all_attempted.set()
                        await release.wait()
                except GenerationAdmissionError as error:
                    assert error.code == TravelResponseErrorCode.CAPACITY_EXCEEDED
                    outcomes.append("rejected")
                    if len(outcomes) == 5:
                        all_attempted.set()

            tasks = [asyncio.create_task(compete()) for _ in range(5)]
            try:
                await asyncio.wait_for(all_attempted.wait(), 5)
                assert outcomes.count("accepted") == 1
                # A different user's request still respects the global limit.
                with pytest.raises(GenerationAdmissionError) as caught:
                    async with gate.reserve(other, message_id=uuid4()):
                        pytest.fail("Global capacity was exceeded")
                assert caught.value.code == TravelResponseErrorCode.CAPACITY_EXCEEDED
            finally:
                release.set()
                await asyncio.gather(*tasks)
            async with gate.reserve(owner, message_id=uuid4()):
                pass
            with pytest.raises(GenerationAdmissionError) as caught:
                async with gate.reserve(owner, message_id=uuid4()):
                    pytest.fail("Daily quota was exceeded")
            assert caught.value.code == TravelResponseErrorCode.DAILY_LIMIT_EXCEEDED
            with pytest.raises(asyncio.CancelledError):
                async with gate.reserve(other, message_id=uuid4()):
                    raise asyncio.CancelledError()
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(GenerationLease)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(GenerationUsage.requests).where(
                            GenerationUsage.user_id == owner
                        )
                    )
                    == 2
                )

                service = ConversationService(session=session)
                first = await service.accept_request(
                    user_id=owner,
                    client_message_id=uuid4(),
                    conversation_id=None,
                    message="Plan Japan",
                    locale="en",
                )
                conversation_id = first.conversation.id
                second = await service.accept_request(
                    user_id=owner,
                    client_message_id=uuid4(),
                    conversation_id=conversation_id,
                    message="Actually plan Turkey",
                    locale="en",
                )
                assert first.user_message.turn_number < second.user_message.turn_number
                repo = PlanningRepository(session)
                messages = MessageRepository(session)
                token = uuid4()
                # Second worker cannot overtake an accepted, unfinished first turn.
                assert (
                    await repo.acquire(
                        conversation_id=conversation_id,
                        user_id=owner,
                        token=token,
                        seconds=120,
                        turn_number=second.user_message.turn_number,
                    )
                    is None
                )
                await session.commit()
                await messages.defer_generation(
                    user_id=owner, message_id=first.user_message.id
                )
                await session.commit()
                assert (
                    await repo.acquire(
                        conversation_id=conversation_id,
                        user_id=owner,
                        token=token,
                        seconds=120,
                        turn_number=second.user_message.turn_number,
                    )
                    is not None
                )
                await repo.release(
                    conversation_id=conversation_id, user_id=owner, token=token
                )
                await session.commit()
                assert (
                    await repo.acquire(
                        conversation_id=conversation_id,
                        user_id=owner,
                        token=token,
                        seconds=120,
                        turn_number=first.user_message.turn_number,
                    )
                    is not None
                )
                await repo.release(
                    conversation_id=conversation_id, user_id=owner, token=token
                )
                messages = MessageRepository(session)
                # Replies may be persisted out of order; history follows user turns.
                second_reply = await messages.create_assistant_message(
                    conversation_id=conversation_id,
                    reply_to_message_id=second.user_message.id,
                    content="Turkey reply",
                )
                first_reply = await messages.create_assistant_message(
                    conversation_id=conversation_id,
                    reply_to_message_id=first.user_message.id,
                    content="Japan reply",
                )
                await session.commit()
                history = await messages.list_recent_by_conversation(
                    conversation_id=conversation_id,
                    user_id=owner,
                    limit=20,
                    through_message_id=first.user_message.id,
                )
                assert [item.id for item in history] == [
                    first.user_message.id,
                    first_reply.id,
                ]
                history = await messages.list_recent_by_conversation(
                    conversation_id=conversation_id,
                    user_id=owner,
                    limit=20,
                    through_message_id=second.user_message.id,
                )
                assert [item.id for item in history] == [
                    first.user_message.id,
                    first_reply.id,
                    second.user_message.id,
                    second_reply.id,
                ]
                assert (
                    await messages.list_recent_by_conversation(
                        conversation_id=conversation_id,
                        user_id=other,
                        limit=20,
                        through_message_id=second.user_message.id,
                    )
                    == []
                )
                assert (
                    await repo.acquire(
                        conversation_id=conversation_id,
                        user_id=owner,
                        token=token,
                        seconds=120,
                        turn_number=second.user_message.turn_number,
                    )
                    is not None
                )
                await repo.stage(
                    conversation_id=conversation_id,
                    user_id=owner,
                    token=token,
                    state=PlanningState(
                        last_turn_number=second.user_message.turn_number
                    ).model_dump(mode="json"),
                    trip_id=None,
                )
                await session.commit()
                with pytest.raises(StalePlanningRequestError):
                    async with ConversationPlanningService(session=session).turn(
                        user_id=owner, accepted=first, lease_seconds=120, wait_seconds=1
                    ):
                        pytest.fail("Old turn replaced newer state")
        finally:
            await engine.dispose()

    asyncio.run(scenario())
