"""Serialize planning turns and stage business state with the final reply."""

import asyncio
import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.trip import Trip
from app.database.repositories.conversations import ConversationRepository
from app.database.repositories.planning import PlanningRepository
from app.database.repositories.trips import TripRepository
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.errors import ConversationNotFoundError, TripNotFoundError
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.trip_requirements import TripRequirements
from app.services.conversation_service import AcceptedTravelRequest


class StalePlanningRequestError(Exception):
    """An older failed turn cannot replace a newer turn's requirements."""


@dataclass(frozen=True, slots=True)
class PlanningTurn:
    token: UUID
    conversation_id: UUID
    user_id: UUID
    state: PlanningState
    trip_id: UUID | None
    preferences: PlanningPreferences = field(default_factory=PlanningPreferences)


class ConversationPlanningService:
    def __init__(self, *, session: AsyncSession) -> None:
        self.session = session
        self.repository = PlanningRepository(session)
        self.trips = TripRepository(session)
        self.preferences = UserPreferenceRepository(session=session)

    @asynccontextmanager
    async def turn(
        self,
        *,
        user_id: UUID,
        accepted: AcceptedTravelRequest,
        lease_seconds: int,
        wait_seconds: float,
    ) -> AsyncGenerator[PlanningTurn, None]:
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
        turn_number = accepted.user_message.turn_number
        delay = 0.25
        try:
            async with asyncio.timeout(wait_seconds):
                while True:
                    lease_deadline = (
                        asyncio.get_running_loop().time() + lease_seconds - 5
                    )
                    snapshot = await self.repository.acquire(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        token=token,
                        seconds=lease_seconds,
                        turn_number=turn_number,
                    )
                    await self.session.commit()
                    if snapshot is not None:
                        acquired = True
                        break
                    await asyncio.sleep(delay + random.uniform(0, delay / 4))
                    delay = min(delay * 2, 2.0)
            payload, trip_id = snapshot
            state = PlanningState.model_validate(payload)
            if turn_number is not None and turn_number < state.last_turn_number:
                raise StalePlanningRequestError()
            if turn_number is not None:
                state = state.model_copy(update={"last_turn_number": turn_number})
            is_requirements_replay = (
                state.requirements_message_id == accepted.user_message.id
            )
            if (
                not is_requirements_replay
                and accepted.trip is not None
                and accepted.trip_id != trip_id
            ):
                trip = accepted.trip
                state = PlanningState(
                    requirements=TripRequirements(
                        origin=trip.origin,
                        destination=trip.destination,
                        start_date=trip.start_date,
                        end_date=trip.end_date,
                    ),
                    phase="collecting",
                    last_turn_number=turn_number or 0,
                )
                trip_id = trip.id
            snapshot = await self.preferences.get_snapshot(user_id=user_id)
            planning_preferences = PlanningPreferences(
                travel_styles=snapshot.travel_styles,
                interests=snapshot.interests,
                budget_tier=snapshot.budget_tier,
                trip_pace=snapshot.trip_pace,
                home_city=(
                    snapshot.home_location.canonical_name
                    if snapshot.home_location is not None
                    else None
                ),
                home_country_code=(
                    snapshot.home_location.country_code
                    if snapshot.home_location is not None
                    else None
                ),
            )
            async with asyncio.timeout_at(lease_deadline):
                yield PlanningTurn(
                    token=token,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    state=state,
                    trip_id=trip_id,
                    preferences=planning_preferences,
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

    async def save_requirements(
        self,
        *,
        turn: PlanningTurn,
        state: PlanningState,
        user_message_id: UUID,
        reset_trip: bool = False,
    ) -> PlanningTurn:
        """Commit requirements and their source message while retaining the lease."""

        if state.phase not in {"collecting", "ready"}:
            raise ValueError("Requirements must be saved in collecting or ready phase")

        persisted_state = PlanningState.model_validate(
            {
                **state.model_dump(),
                "requirements_message_id": user_message_id,
            }
        )
        trip_id = None if reset_trip else turn.trip_id
        try:
            await self.repository.stage(
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                token=turn.token,
                state=persisted_state.model_dump(mode="json"),
                trip_id=trip_id,
                release_lease=False,
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

        return replace(
            turn,
            state=persisted_state,
            trip_id=trip_id,
        )

    async def ensure_context_anchor(
        self,
        *,
        turn: PlanningTurn,
        user_message_id: UUID,
    ) -> PlanningTurn:
        """Persist a trip-context boundary before extraction can fail."""

        state = turn.state.model_copy(
            update={
                "context_start_message_id": (
                    turn.state.context_start_message_id
                    or turn.state.requirements_message_id
                    or user_message_id
                )
            }
        )
        try:
            await self.repository.stage(
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                token=turn.token,
                state=state.model_dump(mode="json"),
                trip_id=turn.trip_id,
                release_lease=False,
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

        return replace(turn, state=state)

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
