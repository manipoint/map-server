"""Tests for assistant processing lease persistence."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.assistant_run import AssistantRun
from app.database.repositories.assistant_runs import AssistantRunRepository
from app.domain.enums import AssistantRunStatus


def create_mock_session() -> Mock:
    """Create a mocked asynchronous database session."""

    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def create_query_result(row) -> Mock:
    """Create a result returning one optional SQLAlchemy row."""

    result = Mock()
    result.one_or_none.return_value = row
    result.scalar_one_or_none.return_value = row
    return result


def create_run(
    *,
    user_message_id: UUID,
    status: AssistantRunStatus = AssistantRunStatus.PROCESSING,
    attempt_count: int = 1,
) -> AssistantRun:
    """Create an in-memory assistant processing run."""

    return AssistantRun(
        id=uuid4(),
        user_message_id=user_message_id,
        assistant_message_id=None,
        status=status.value,
        claim_token=uuid4() if status is AssistantRunStatus.PROCESSING else None,
        lease_expires_at=(
            datetime.now(UTC) + timedelta(minutes=1)
            if status is AssistantRunStatus.PROCESSING
            else None
        ),
        attempt_count=attempt_count,
        last_error_code=(
            "provider_error" if status is AssistantRunStatus.FAILED else None
        ),
    )


def test_acquire_claim_returns_a_newly_inserted_claim() -> None:
    """The first worker should atomically insert and own the processing lease."""

    user_message_id = uuid4()
    observed_at = datetime.now(UTC)
    run = create_run(user_message_id=user_message_id)
    session = create_mock_session()
    session.execute.return_value = create_query_result((run, observed_at))
    repository = AssistantRunRepository(session)

    claim = asyncio.run(
        repository.acquire_claim(
            user_message_id=user_message_id,
            lease_duration=timedelta(seconds=60),
            max_attempts=3,
        )
    )

    assert claim.run is run
    assert claim.acquired is True
    assert claim.claim_token is not None
    assert claim.observed_at is observed_at
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_new_claim_uses_atomic_postgresql_conflict_handling() -> None:
    """Claim creation should target the unique user-message constraint."""

    user_message_id = uuid4()
    run = create_run(user_message_id=user_message_id)
    session = create_mock_session()
    session.execute.return_value = create_query_result((run, datetime.now(UTC)))
    repository = AssistantRunRepository(session)

    claim = asyncio.run(
        repository.acquire_claim(
            user_message_id=user_message_id,
            lease_duration=timedelta(seconds=45),
            max_attempts=3,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled)
    parameter_values = compiled.params.values()
    assert "INSERT INTO app.assistant_runs" in compiled_sql
    assert (
        "ON CONFLICT ON CONSTRAINT uq_assistant_runs_user_message_id DO NOTHING"
        in compiled_sql
    )
    assert "lease_expires_at" in compiled_sql
    assert "RETURNING" in compiled_sql
    assert user_message_id in parameter_values
    assert timedelta(seconds=45) in parameter_values
    assert AssistantRunStatus.PROCESSING.value in parameter_values
    assert claim.claim_token in parameter_values


def test_acquire_claim_returns_an_existing_active_run_without_ownership() -> None:
    """A second worker should observe an existing run without its claim token."""

    user_message_id = uuid4()
    observed_at = datetime.now(UTC)
    run = create_run(user_message_id=user_message_id)
    session = create_mock_session()
    session.execute.side_effect = [
        create_query_result(None),
        create_query_result(None),
        create_query_result((run, observed_at)),
    ]
    repository = AssistantRunRepository(session)

    claim = asyncio.run(
        repository.acquire_claim(
            user_message_id=user_message_id,
            lease_duration=timedelta(seconds=60),
            max_attempts=3,
        )
    )

    assert claim.run is run
    assert claim.acquired is False
    assert claim.claim_token is None
    assert claim.observed_at is observed_at
    assert session.execute.await_count == 3


def test_acquire_claim_does_not_reclaim_a_completed_run() -> None:
    """A completed run should be returned for canonical reply reuse."""

    user_message_id = uuid4()
    observed_at = datetime.now(UTC)
    run = create_run(
        user_message_id=user_message_id,
        status=AssistantRunStatus.COMPLETED,
    )
    run.assistant_message_id = uuid4()
    session = create_mock_session()
    session.execute.side_effect = [
        create_query_result(None),
        create_query_result(None),
        create_query_result((run, observed_at)),
    ]
    repository = AssistantRunRepository(session)

    claim = asyncio.run(
        repository.acquire_claim(
            user_message_id=user_message_id,
            lease_duration=timedelta(seconds=60),
            max_attempts=3,
        )
    )

    assert claim.run.status == AssistantRunStatus.COMPLETED.value
    assert claim.acquired is False
    assert claim.claim_token is None


def test_acquire_claim_returns_an_atomic_reclaimed_lease() -> None:
    """An expired or failed run with attempts remaining should be reclaimed."""

    user_message_id = uuid4()
    observed_at = datetime.now(UTC)
    reclaimed_run = create_run(user_message_id=user_message_id, attempt_count=2)
    session = create_mock_session()
    session.execute.side_effect = [
        create_query_result(None),
        create_query_result((reclaimed_run, observed_at)),
    ]
    repository = AssistantRunRepository(session)

    claim = asyncio.run(
        repository.acquire_claim(
            user_message_id=user_message_id,
            lease_duration=timedelta(seconds=90),
            max_attempts=3,
        )
    )

    assert claim.run is reclaimed_run
    assert claim.acquired is True
    assert claim.claim_token is not None
    assert session.execute.await_count == 2

    statement = session.execute.await_args_list[1].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled)
    parameter_values = compiled.params.values()
    assert "UPDATE app.assistant_runs" in compiled_sql
    assert "assistant_runs.attempt_count <" in compiled_sql
    assert "assistant_runs.lease_expires_at <= now()" in compiled_sql
    assert user_message_id in parameter_values
    assert timedelta(seconds=90) in parameter_values
    assert AssistantRunStatus.FAILED.value in parameter_values
    assert AssistantRunStatus.PROCESSING.value in parameter_values
    assert claim.claim_token in parameter_values


def test_acquire_claim_returns_an_exhausted_run_without_reclaiming() -> None:
    """A run at the attempt limit should remain unowned by the caller."""

    user_message_id = uuid4()
    run = create_run(
        user_message_id=user_message_id,
        status=AssistantRunStatus.FAILED,
        attempt_count=3,
    )
    session = create_mock_session()
    session.execute.side_effect = [
        create_query_result(None),
        create_query_result(None),
        create_query_result((run, datetime.now(UTC))),
    ]
    repository = AssistantRunRepository(session)

    claim = asyncio.run(
        repository.acquire_claim(
            user_message_id=user_message_id,
            lease_duration=timedelta(seconds=60),
            max_attempts=3,
        )
    )

    assert claim.run.attempt_count == 3
    assert claim.acquired is False
    assert claim.claim_token is None


def test_acquire_claim_raises_if_the_conflicting_run_disappears() -> None:
    """An impossible missing fallback row should surface as an invariant failure."""

    session = create_mock_session()
    session.execute.side_effect = [
        create_query_result(None),
        create_query_result(None),
        create_query_result(None),
    ]
    repository = AssistantRunRepository(session)

    with pytest.raises(
        RuntimeError,
        match="Assistant run disappeared during claim acquisition",
    ):
        asyncio.run(
            repository.acquire_claim(
                user_message_id=uuid4(),
                lease_duration=timedelta(seconds=60),
                max_attempts=3,
            )
        )


@pytest.mark.parametrize(
    "lease_duration",
    [timedelta(0), timedelta(seconds=-1)],
)
def test_acquire_claim_rejects_a_non_positive_lease(
    lease_duration: timedelta,
) -> None:
    """An invalid lease should fail before executing database work."""

    session = create_mock_session()
    repository = AssistantRunRepository(session)

    with pytest.raises(ValueError, match="lease_duration must be greater than zero"):
        asyncio.run(
            repository.acquire_claim(
                user_message_id=uuid4(),
                lease_duration=lease_duration,
                max_attempts=3,
            )
        )

    session.execute.assert_not_awaited()


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_acquire_claim_rejects_non_positive_max_attempts(max_attempts: int) -> None:
    """An invalid retry limit should fail before executing database work."""

    session = create_mock_session()
    repository = AssistantRunRepository(session)

    with pytest.raises(ValueError, match="max_attempts must be greater than zero"):
        asyncio.run(
            repository.acquire_claim(
                user_message_id=uuid4(),
                lease_duration=timedelta(seconds=60),
                max_attempts=max_attempts,
            )
        )

    session.execute.assert_not_awaited()


def test_complete_run_updates_only_the_owned_processing_claim() -> None:
    """The owning worker should atomically link its assistant message."""

    run_id = uuid4()
    claim_token = uuid4()
    assistant_message_id = uuid4()
    completed_run = create_run(
        user_message_id=uuid4(),
        status=AssistantRunStatus.COMPLETED,
    )
    completed_run.id = run_id
    completed_run.assistant_message_id = assistant_message_id
    session = create_mock_session()
    session.execute.return_value = create_query_result(completed_run)
    repository = AssistantRunRepository(session)

    result = asyncio.run(
        repository.complete_run(
            run_id=run_id,
            claim_token=claim_token,
            assistant_message_id=assistant_message_id,
        )
    )

    assert result is completed_run
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled)
    parameter_values = compiled.params.values()
    assert "UPDATE app.assistant_runs" in compiled_sql
    assert "assistant_runs.id =" in compiled_sql
    assert "assistant_runs.status =" in compiled_sql
    assert "assistant_runs.claim_token =" in compiled_sql
    assert "RETURNING" in compiled_sql
    assert run_id in parameter_values
    assert claim_token in parameter_values
    assert assistant_message_id in parameter_values
    assert AssistantRunStatus.PROCESSING.value in parameter_values
    assert AssistantRunStatus.COMPLETED.value in parameter_values
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_complete_run_returns_none_for_a_stale_or_missing_claim() -> None:
    """A stale worker should be unable to complete a reclaimed run."""

    session = create_mock_session()
    session.execute.return_value = create_query_result(None)
    repository = AssistantRunRepository(session)

    result = asyncio.run(
        repository.complete_run(
            run_id=uuid4(),
            claim_token=uuid4(),
            assistant_message_id=uuid4(),
        )
    )

    assert result is None
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_fail_run_updates_only_the_owned_claim_with_a_safe_error_code() -> None:
    """The owning worker should atomically release and fail its processing run."""

    run_id = uuid4()
    claim_token = uuid4()
    failed_run = create_run(
        user_message_id=uuid4(),
        status=AssistantRunStatus.FAILED,
    )
    failed_run.id = run_id
    session = create_mock_session()
    session.execute.return_value = create_query_result(failed_run)
    repository = AssistantRunRepository(session)

    result = asyncio.run(
        repository.fail_run(
            run_id=run_id,
            claim_token=claim_token,
            error_code="  model_timeout  ",
        )
    )

    assert result is failed_run
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled)
    parameter_values = compiled.params.values()
    assert "UPDATE app.assistant_runs" in compiled_sql
    assert "assistant_runs.id =" in compiled_sql
    assert "assistant_runs.status =" in compiled_sql
    assert "assistant_runs.claim_token =" in compiled_sql
    assert "RETURNING" in compiled_sql
    assert run_id in parameter_values
    assert claim_token in parameter_values
    assert "model_timeout" in parameter_values
    assert AssistantRunStatus.PROCESSING.value in parameter_values
    assert AssistantRunStatus.FAILED.value in parameter_values
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_fail_run_returns_none_for_a_stale_or_missing_claim() -> None:
    """A stale worker should be unable to fail a reclaimed run."""

    session = create_mock_session()
    session.execute.return_value = create_query_result(None)
    repository = AssistantRunRepository(session)

    result = asyncio.run(
        repository.fail_run(
            run_id=uuid4(),
            claim_token=uuid4(),
            error_code="provider_error",
        )
    )

    assert result is None
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("error_code", ["", "   ", "\n\t"])
def test_fail_run_rejects_a_blank_error_code(error_code: str) -> None:
    """A blank failure code should be rejected before database work."""

    session = create_mock_session()
    repository = AssistantRunRepository(session)

    with pytest.raises(ValueError, match="error_code must not be blank"):
        asyncio.run(
            repository.fail_run(
                run_id=uuid4(),
                claim_token=uuid4(),
                error_code=error_code,
            )
        )

    session.execute.assert_not_awaited()


def test_fail_run_rejects_an_error_code_over_the_database_limit() -> None:
    """Failure codes longer than the model column should be rejected early."""

    session = create_mock_session()
    repository = AssistantRunRepository(session)

    with pytest.raises(
        ValueError,
        match="error_code must not exceed 64 characters",
    ):
        asyncio.run(
            repository.fail_run(
                run_id=uuid4(),
                claim_token=uuid4(),
                error_code="x" * 65,
            )
        )

    session.execute.assert_not_awaited()
