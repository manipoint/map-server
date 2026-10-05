"""Planning persistence boundaries and lease cleanup."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.domain.errors import ConversationNotFoundError
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.preferences import (
    BudgetTier,
    TravelInterest,
    TravelStyle,
    TripPace,
)
from app.domain.trip_requirements import TripRequirements
from app.domain.trips import CanonicalLocation
from app.services.conversation_planning_service import (
    ConversationPlanningService,
    PlanningTurn,
)


def service_and_request(monkeypatch):
    session = AsyncMock()
    repository = AsyncMock()
    repository.get_by_id_for_user.return_value = object()
    monkeypatch.setattr(
        "app.services.conversation_planning_service.ConversationRepository",
        lambda _: repository,
    )
    service = ConversationPlanningService(session=session)
    service.repository = AsyncMock()
    service.repository.acquire.return_value = ({}, None)
    service.trips = AsyncMock()
    service.preferences = AsyncMock()
    service.preferences.get_snapshot.return_value = SimpleNamespace(
        travel_styles=(),
        interests=(),
        budget_tier=None,
        trip_pace=None,
        home_location=None,
    )
    accepted = SimpleNamespace(
        conversation=SimpleNamespace(id=uuid4()),
        trip=None,
        trip_id=None,
        user_message=SimpleNamespace(id=uuid4(), turn_number=1),
    )
    return service, accepted, repository


def test_turn_loads_profile_preferences_for_the_authenticated_user(monkeypatch):
    service, accepted, _ = service_and_request(monkeypatch)
    profile = SimpleNamespace(
        travel_styles=(TravelStyle.NATURE,),
        interests=(TravelInterest.HIKING, TravelInterest.WILDLIFE),
        budget_tier=BudgetTier.BUDGET,
        trip_pace=TripPace.RELAXED,
        home_location=CanonicalLocation(
            provider="google",
            provider_location_id="lahore-test-id",
            canonical_name="Lahore",
            country_code="PK",
            latitude=31.5204,
            longitude=74.3587,
        ),
    )
    service.preferences.get_snapshot.return_value = profile
    user_id = uuid4()

    async def scenario():
        async with service.turn(
            user_id=user_id,
            accepted=accepted,
            lease_seconds=120,
            wait_seconds=1,
        ) as turn:
            assert turn.preferences == PlanningPreferences(
                travel_styles=profile.travel_styles,
                interests=profile.interests,
                budget_tier=profile.budget_tier,
                trip_pace=profile.trip_pace,
                home_city="Lahore",
                home_country_code="PK",
            )

    asyncio.run(scenario())

    service.preferences.get_snapshot.assert_awaited_once_with(user_id=user_id)


def test_turn_without_onboarding_preferences_uses_empty_planning_preferences(
    monkeypatch,
):
    service, accepted, _ = service_and_request(monkeypatch)

    async def scenario():
        async with service.turn(
            user_id=uuid4(),
            accepted=accepted,
            lease_seconds=120,
            wait_seconds=1,
        ) as turn:
            assert turn.preferences == PlanningPreferences()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", [None, ValueError("invalid model result"), asyncio.CancelledError()]
)
def test_lease_cleanup_on_success_failure_and_cancellation(monkeypatch, failure):
    service, accepted, _ = service_and_request(monkeypatch)

    async def scenario():
        async with service.turn(
            user_id=uuid4(), accepted=accepted, lease_seconds=120, wait_seconds=1
        ) as turn:
            assert turn.state == PlanningState(last_turn_number=1)
            assert service.session.commit.await_count == 2
            if failure is not None:
                raise failure

    if failure is not None:
        with pytest.raises(type(failure)):
            asyncio.run(scenario())
        service.session.rollback.assert_awaited_once()
    else:
        asyncio.run(scenario())
        service.session.rollback.assert_not_awaited()
    service.repository.release.assert_awaited_once()


def test_foreign_conversation_never_acquires_lease(monkeypatch):
    service, accepted, conversations = service_and_request(monkeypatch)
    conversations.get_by_id_for_user.return_value = None

    async def scenario():
        async with service.turn(
            user_id=uuid4(), accepted=accepted, lease_seconds=120, wait_seconds=1
        ):
            pytest.fail("Unauthorized conversation entered")

    with pytest.raises(ConversationNotFoundError):
        asyncio.run(scenario())
    service.repository.acquire.assert_not_awaited()


def test_busy_conversation_wait_is_bounded_and_does_not_release_other_owner(
    monkeypatch,
):
    service, accepted, _ = service_and_request(monkeypatch)
    service.repository.acquire.return_value = None

    async def scenario():
        async with service.turn(
            user_id=uuid4(), accepted=accepted, lease_seconds=120, wait_seconds=0.01
        ):
            pytest.fail("Busy conversation entered")

    with pytest.raises(TimeoutError):
        asyncio.run(scenario())
    service.repository.release.assert_not_awaited()


def test_turn_deadline_covers_pre_graph_work_and_releases_lease(monkeypatch):
    service, accepted, _ = service_and_request(monkeypatch)

    async def scenario():
        async with service.turn(
            user_id=uuid4(), accepted=accepted, lease_seconds=5, wait_seconds=1
        ):
            await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        asyncio.run(scenario())
    service.repository.release.assert_awaited_once()


def test_generation_stages_trip_and_state_without_committing(monkeypatch):
    service, _, _ = service_and_request(monkeypatch)
    trip = Mock(id=uuid4())
    service.trips.create.return_value = trip
    state = PlanningState(
        requirements=TripRequirements(
            destination="Japan", start_date="2099-11-07", duration_days=5
        )
    )
    turn = PlanningTurn(
        token=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        state=PlanningState(),
        trip_id=None,
    )
    result = asyncio.run(service.stage(turn=turn, state=state, generated=True))
    assert result is trip
    assert service.repository.stage.await_args.kwargs["trip_id"] == trip.id
    assert service.trips.create.await_args.kwargs["user_id"] == turn.user_id
    service.session.commit.assert_not_awaited()


def test_clarification_only_stages_requirements(monkeypatch):
    service, _, _ = service_and_request(monkeypatch)
    turn = PlanningTurn(
        token=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        state=PlanningState(),
        trip_id=uuid4(),
    )
    result = asyncio.run(
        service.stage(
            turn=turn, state=PlanningState(), generated=False, reset_trip=True
        )
    )
    assert result is None
    service.trips.create.assert_not_awaited()
    assert service.repository.stage.await_args.kwargs["trip_id"] is None
    service.session.commit.assert_not_awaited()


def requirements_turn():
    return PlanningTurn(
        token=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        state=PlanningState(),
        trip_id=uuid4(),
    )


@pytest.mark.parametrize("phase", ["collecting", "ready"])
@pytest.mark.parametrize("reset_trip", [False, True])
def test_save_requirements_commits_without_releasing_lease_or_creating_trip(
    monkeypatch, phase, reset_trip
):
    service, _, _ = service_and_request(monkeypatch)
    turn = requirements_turn()
    state = PlanningState(
        phase=phase, revision=1, requirements=TripRequirements(destination="Japan")
    )
    user_message_id = uuid4()
    persisted_state = state.model_copy(
        update={"requirements_message_id": user_message_id}
    )
    operations = []

    async def stage(**kwargs):
        operations.append("stage")

    async def commit():
        operations.append("commit")

    service.repository.stage.side_effect = stage
    service.session.commit.side_effect = commit
    saved = asyncio.run(
        service.save_requirements(
            turn=turn,
            state=state,
            reset_trip=reset_trip,
            user_message_id=user_message_id,
        )
    )
    service.repository.stage.assert_awaited_once_with(
        conversation_id=turn.conversation_id,
        user_id=turn.user_id,
        token=turn.token,
        state=persisted_state.model_dump(mode="json"),
        trip_id=None if reset_trip else turn.trip_id,
        release_lease=False,
    )
    assert operations == ["stage", "commit"]
    assert saved.state == persisted_state
    assert state.requirements_message_id is None
    assert saved.trip_id == (None if reset_trip else turn.trip_id)
    assert saved.token == turn.token
    assert saved.user_id == turn.user_id
    assert saved.conversation_id == turn.conversation_id
    assert saved is not turn and turn.state == PlanningState()
    assert turn.trip_id is not None
    service.repository.release.assert_not_awaited()
    service.trips.create.assert_not_awaited()
    service.session.rollback.assert_not_awaited()


@pytest.mark.parametrize("phase", ["idle", "generated"])
def test_invalid_save_phase_does_not_touch_database(monkeypatch, phase):
    service, _, _ = service_and_request(monkeypatch)
    with pytest.raises(ValueError, match="collecting or ready"):
        asyncio.run(
            service.save_requirements(
                turn=requirements_turn(),
                state=PlanningState(phase=phase),
                user_message_id=uuid4(),
            )
        )
    service.repository.stage.assert_not_awaited()
    service.session.commit.assert_not_awaited()
    service.session.rollback.assert_not_awaited()


@pytest.mark.parametrize("failure_stage", ["stage", "commit"])
@pytest.mark.parametrize(
    "error", [RuntimeError("lease or database failure"), asyncio.CancelledError()]
)
def test_save_failure_rolls_back_and_propagates(monkeypatch, failure_stage, error):
    service, _, _ = service_and_request(monkeypatch)
    target = (
        service.repository.stage if failure_stage == "stage" else service.session.commit
    )
    target.side_effect = error
    with pytest.raises(type(error)) as raised:
        asyncio.run(
            service.save_requirements(
                turn=requirements_turn(),
                state=PlanningState(phase="collecting"),
                user_message_id=uuid4(),
            )
        )
    assert raised.value is error
    service.session.rollback.assert_awaited_once()
    if failure_stage == "stage":
        service.session.commit.assert_not_awaited()
    service.repository.release.assert_not_awaited()


def test_replay_does_not_restore_old_trip_after_saved_reset(monkeypatch):
    service, accepted, _ = service_and_request(monkeypatch)
    state = PlanningState(
        phase="collecting",
        requirements_message_id=accepted.user_message.id,
        requirements=TripRequirements(destination="Hunza"),
    )
    service.repository.acquire.return_value = (state.model_dump(mode="json"), None)
    accepted.trip = SimpleNamespace(id=uuid4())
    accepted.trip_id = accepted.trip.id

    async def scenario():
        async with service.turn(
            user_id=uuid4(), accepted=accepted, lease_seconds=120, wait_seconds=1
        ) as turn:
            assert turn.state == state.model_copy(update={"last_turn_number": 1})
            assert turn.trip_id is None

    asyncio.run(scenario())
