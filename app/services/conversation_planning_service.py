"""Serialize planning turns and stage business state with the final reply."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.trip import Trip
from app.database.repositories.conversations import ConversationRepository
from app.database.repositories.planning import PlanningRepository
from app.database.repositories.trips import TripRepository
from app.domain.errors import ConversationNotFoundError, TripNotFoundError
from app.domain.planning import PlanningState
from app.domain.trip_requirements import TripRequirements
from app.services.conversation_service import AcceptedTravelRequest


@dataclass(frozen=True, slots=True)
class PlanningTurn:
    token: UUID
    conversation_id: UUID
    user_id: UUID
    state: PlanningState
    trip_id: UUID | None


class ConversationPlanningService:
    def __init__(self, *, session: AsyncSession) -> None:
        self.session = session
        self.repository = PlanningRepository(session)
        self.trips = TripRepository(session)

    @asynccontextmanager
    async def turn(
        self,
        *,
        user_id: UUID,
        accepted: AcceptedTravelRequest,
        lease_seconds: int,
        wait_seconds: float,
    ) -> AsyncIterator[PlanningTurn]:
        conversation_id = accepted.conversation.id
        token = uuid4()
        # Authorize before waiting; a missing/foreign conversation cannot spin.
        conversation = await ConversationRepository(self.session).get_by_id_for_user(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise ConversationNotFoundError("Conversation was not found")
        await self.session.commit()
        acquired = False
        try:
            async with asyncio.timeout(wait_seconds):
                while True:
                    snapshot = await self.repository.acquire(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        token=token,
                        seconds=lease_seconds,
                    )
                    await self.session.commit()
                    if snapshot is not None:
                        acquired = True
                        break
                    await asyncio.sleep(0.25)
            payload, trip_id = snapshot
            state = PlanningState.model_validate(payload)
            if accepted.trip is not None and accepted.trip.id != trip_id:
                trip = accepted.trip
                state = PlanningState(
                    requirements=TripRequirements(
                        origin=trip.origin,
                        destination=trip.destination,
                        start_date=trip.start_date,
                        end_date=trip.end_date,
                    ),
                    phase="collecting",
                )
                trip_id = trip.id
            yield PlanningTurn(
                token=token,
                conversation_id=conversation_id,
                user_id=user_id,
                state=state,
                trip_id=trip_id,
            )
        except BaseException:
            await self.session.rollback()
            raise
        finally:
            if acquired:
                await self.repository.release(
                    conversation_id=conversation_id, user_id=user_id, token=token
                )
                await self.session.commit()

    async def stage(
        self,
        *,
        turn: PlanningTurn,
        state: PlanningState,
        generated: bool,
        reset_trip: bool = False,
    ) -> Trip | None:
        trip = None
        trip_id = None if reset_trip else turn.trip_id
        requirements = state.requirements
        if generated:
            if trip_id is not None:
                trip = await self.trips.get_by_id_for_user(
                    trip_id=trip_id, user_id=turn.user_id, for_update=True
                )
                if trip is None:
                    raise TripNotFoundError("Trip was not found")
            if trip is None or (
                trip.destination,
                trip.origin,
                trip.start_date,
                trip.end_date,
            ) != (
                requirements.destination,
                requirements.origin,
                requirements.start_date,
                requirements.resolved_end_date,
            ):
                # Preserve existing saved trips when the conversation changes route/dates.
                trip = await self.trips.create(
                    user_id=turn.user_id,
                    origin=requirements.origin,
                    destination=requirements.destination,
                    start_date=requirements.start_date,
                    end_date=requirements.resolved_end_date,
                )
                trip_id = trip.id
        await self.repository.stage(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            token=turn.token,
            state=state.model_dump(mode="json"),
            trip_id=trip_id,
        )
        return trip
