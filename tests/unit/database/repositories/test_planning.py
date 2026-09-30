"""Planning lease SQL must enforce ownership and stale-worker fencing."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.database.repositories.planning import PlanningRepository


def test_acquire_is_atomic_owner_scoped_and_uses_database_clock():
    session = AsyncMock()
    result = Mock()
    result.one_or_none.return_value = ({}, None)
    session.execute.return_value = result
    owner, conversation, token = uuid4(), uuid4(), uuid4()
    snapshot = asyncio.run(
        PlanningRepository(session).acquire(
            conversation_id=conversation, user_id=owner, token=token, seconds=120
        )
    )
    assert snapshot == ({}, None)
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert {owner, conversation, token}.issubset(set(compiled.params.values()))
    assert "clock_timestamp()" in str(compiled)
    assert "planning_lease_token IS NULL OR" in str(compiled)
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("release_lease", [True, False])
def test_stale_worker_cannot_stage_state(release_lease):
    session = AsyncMock()
    result = Mock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    with pytest.raises(RuntimeError, match="lease"):
        asyncio.run(
            PlanningRepository(session).stage(
                conversation_id=uuid4(),
                user_id=uuid4(),
                token=uuid4(),
                state={},
                trip_id=None,
                release_lease=release_lease,
            )
        )
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "planning_lease_expires_at > clock_timestamp()" in sql
    assert "conversations.user_id =" in sql
    assert "conversations.planning_lease_token =" in sql
    session.commit.assert_not_awaited()


def test_release_never_unlocks_a_replacement_worker():
    session = AsyncMock()
    token = uuid4()
    asyncio.run(
        PlanningRepository(session).release(
            conversation_id=uuid4(), user_id=uuid4(), token=token
        )
    )
    statement = session.execute.await_args.args[0]
    assert token in statement.compile().params.values()
    assert "conversations.planning_lease_token =" in str(statement)


@pytest.mark.parametrize("release_lease", [True, False])
def test_stage_retains_or_releases_lease_without_weakening_owner_fence(release_lease):
    session = AsyncMock()
    result = Mock()
    conversation, owner, token, trip = uuid4(), uuid4(), uuid4(), uuid4()
    result.scalar_one_or_none.return_value = conversation
    session.execute.return_value = result
    state = {"phase": "collecting", "revision": 1}
    asyncio.run(
        PlanningRepository(session).stage(
            conversation_id=conversation,
            user_id=owner,
            token=token,
            state=state,
            trip_id=trip,
            release_lease=release_lease,
        )
    )
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert ("planning_lease_token=" in assignments) is release_lease
    assert ("planning_lease_expires_at=" in assignments) is release_lease
    assert compiled.params["planning_state"] == state
    assert compiled.params["planning_trip_id"] == trip
    assert "conversations.user_id =" in sql
    assert "conversations.id =" in sql
    assert "conversations.planning_lease_token =" in sql
    assert "planning_lease_expires_at > clock_timestamp()" in sql
    assert all(
        value in compiled.params.values() for value in (conversation, owner, token)
    )
    session.commit.assert_not_awaited()
