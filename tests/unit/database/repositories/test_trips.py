"""Tests for the trip repository."""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.trip import Trip
from app.database.repositories.trips import TripPage, TripRepository
from app.domain.trips import TripStatus


def create_mock_session() -> Mock:
    """Create an asynchronous database-session mock."""

    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def test_create_adds_and_flushes_draft_trip_without_commit() -> None:
    """Creating a trip should flush while leaving commit to the service."""

    user_id = uuid4()
    session = create_mock_session()
    repository = TripRepository(session)

    trip = asyncio.run(
        repository.create(
            user_id=user_id,
            title="London museums",
            origin="Lahore",
            destination="London",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
        )
    )

    assert trip.user_id == user_id
    assert trip.title == "London museums"
    assert trip.origin == "Lahore"
    assert trip.destination == "London"
    assert trip.start_date == date(2026, 9, 10)
    assert trip.end_date == date(2026, 9, 12)
    assert trip.status == TripStatus.DRAFT.value
    session.add.assert_called_once_with(trip)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_owner_lookup_returns_matching_trip() -> None:
    """An existing user-owned trip should be returned."""

    trip = Mock(spec=Trip)
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = trip
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = TripRepository(session)

    result = asyncio.run(
        repository.get_by_id_for_user(
            trip_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert result is trip
    session.execute.assert_awaited_once()


def test_owner_lookup_returns_none_for_missing_or_differently_owned_trip() -> None:
    """A missing trip or another user's trip should not be exposed."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = TripRepository(session)

    result = asyncio.run(
        repository.get_by_id_for_user(
            trip_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert result is None


def test_owner_lookup_filters_by_trip_and_user() -> None:
    """Trip lookup must include identity and ownership predicates."""

    trip_id = uuid4()
    user_id = uuid4()
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = TripRepository(session)

    asyncio.run(
        repository.get_by_id_for_user(
            trip_id=trip_id,
            user_id=user_id,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled_statement = statement.compile()

    assert statement.column_descriptions[0]["entity"] is Trip
    assert trip_id in compiled_statement.params.values()
    assert user_id in compiled_statement.params.values()
    assert "trips.id" in str(compiled_statement)
    assert "trips.user_id" in str(compiled_statement)


def test_owner_lookup_does_not_lock_by_default() -> None:
    """Read-only trip lookup should not acquire a row lock."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = TripRepository(session)

    asyncio.run(
        repository.get_by_id_for_user(
            trip_id=uuid4(),
            user_id=uuid4(),
        )
    )

    statement = session.execute.await_args.args[0]
    assert "FOR UPDATE" not in str(statement.compile())


def test_owner_lookup_can_lock_trip() -> None:
    """Trip mutations should be able to lock the selected row."""

    query_result = Mock()
    query_result.scalar_one_or_none.return_value = None
    session = create_mock_session()
    session.execute.return_value = query_result
    repository = TripRepository(session)

    asyncio.run(
        repository.get_by_id_for_user(
            trip_id=uuid4(),
            user_id=uuid4(),
            for_update=True,
        )
    )

    statement = session.execute.await_args.args[0]
    assert "FOR UPDATE" in str(statement.compile())


def create_trip_query_result(trips: list[Trip]) -> Mock:
    """Create a query result that returns the supplied trip rows."""

    scalars = Mock()
    scalars.all.return_value = trips
    query_result = Mock()
    query_result.scalars.return_value = scalars
    return query_result


def test_list_for_user_returns_trimmed_page_and_has_more() -> None:
    """One look-ahead row should signal another page without being returned."""

    trips = [Mock(spec=Trip) for _ in range(3)]
    session = create_mock_session()
    session.execute.return_value = create_trip_query_result(trips)
    repository = TripRepository(session)

    page = asyncio.run(repository.list_for_user(user_id=uuid4(), limit=2))

    assert page == TripPage(items=trips[:2], has_more=True)


def test_list_for_user_reports_last_page() -> None:
    """A page without a look-ahead row should be marked as final."""

    trips = [Mock(spec=Trip)]
    session = create_mock_session()
    session.execute.return_value = create_trip_query_result(trips)
    repository = TripRepository(session)

    page = asyncio.run(repository.list_for_user(user_id=uuid4(), limit=2))

    assert page == TripPage(items=trips, has_more=False)


def test_list_for_user_filters_owner_status_and_uses_lookahead_limit() -> None:
    """Trip pages should be scoped, filtered, stable, and bounded."""

    user_id = uuid4()
    session = create_mock_session()
    session.execute.return_value = create_trip_query_result([])
    repository = TripRepository(session)

    asyncio.run(
        repository.list_for_user(
            user_id=user_id,
            status=TripStatus.PLANNED,
            limit=20,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled_statement = statement.compile()
    compiled_sql = str(compiled_statement)

    assert statement.column_descriptions[0]["entity"] is Trip
    assert user_id in compiled_statement.params.values()
    assert TripStatus.PLANNED.value in compiled_statement.params.values()
    assert 21 in compiled_statement.params.values()
    assert "trips.user_id" in compiled_sql
    assert "trips.status" in compiled_sql
    assert "ORDER BY app.trips.updated_at DESC, app.trips.id DESC" in compiled_sql


def test_list_for_user_applies_keyset_cursor() -> None:
    """A complete cursor should seek after its timestamp and UUID tuple."""

    before_updated_at = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
    before_id = uuid4()
    session = create_mock_session()
    session.execute.return_value = create_trip_query_result([])
    repository = TripRepository(session)

    asyncio.run(
        repository.list_for_user(
            user_id=uuid4(),
            before_updated_at=before_updated_at,
            before_id=before_id,
        )
    )

    statement = session.execute.await_args.args[0]
    compiled_statement = statement.compile()
    compiled_sql = str(compiled_statement)

    assert before_updated_at in compiled_statement.params.values()
    assert before_id in compiled_statement.params.values()
    assert "trips.updated_at <" in compiled_sql
    assert "trips.updated_at =" in compiled_sql
    assert "trips.id <" in compiled_sql


@pytest.mark.parametrize("limit", [0, 101])
def test_list_for_user_rejects_out_of_range_limit(limit: int) -> None:
    """Trip pages should reject unbounded or empty page sizes."""

    repository = TripRepository(create_mock_session())

    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        asyncio.run(repository.list_for_user(user_id=uuid4(), limit=limit))


@pytest.mark.parametrize(
    ("before_updated_at", "before_id"),
    [
        (datetime(2026, 8, 26, 12, 30, tzinfo=UTC), None),
        (None, uuid4()),
    ],
)
def test_list_for_user_rejects_incomplete_cursor(
    before_updated_at: datetime | None,
    before_id: UUID | None,
) -> None:
    """Both keyset values should be required to preserve deterministic paging."""

    repository = TripRepository(create_mock_session())

    with pytest.raises(
        ValueError,
        match="before_updated_at and before_id must be provided together",
    ):
        asyncio.run(
            repository.list_for_user(
                user_id=uuid4(),
                before_updated_at=before_updated_at,
                before_id=before_id,
            )
        )


def test_update_details_replaces_fields_and_flushes_without_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trip detail replacement should refresh its cursor timestamp and flush."""

    original_updated_at = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
    updated_at = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
    monkeypatch.setattr(
        "app.database.repositories.trips.utc_now",
        lambda: updated_at,
    )
    trip = Trip(
        user_id=uuid4(),
        title="Old title",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        status=TripStatus.DRAFT.value,
        updated_at=original_updated_at,
    )
    session = create_mock_session()
    repository = TripRepository(session)

    result = asyncio.run(
        repository.update_details(
            trip=trip,
            title=None,
            origin=None,
            destination="Paris",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 5),
        )
    )

    assert result is trip
    assert trip.title is None
    assert trip.origin is None
    assert trip.destination == "Paris"
    assert trip.start_date == date(2026, 10, 1)
    assert trip.end_date == date(2026, 10, 5)
    assert trip.updated_at == updated_at
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "status",
    [TripStatus.PLANNED, TripStatus.ARCHIVED],
)
def test_set_status_persists_enum_value_and_refreshes_timestamp(
    status: TripStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status changes should store plain values and refresh cursor ordering."""

    updated_at = datetime(2026, 8, 27, 10, 30, tzinfo=UTC)
    monkeypatch.setattr(
        "app.database.repositories.trips.utc_now",
        lambda: updated_at,
    )
    trip = Trip(
        user_id=uuid4(),
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        status=TripStatus.DRAFT.value,
    )
    session = create_mock_session()
    repository = TripRepository(session)

    result = asyncio.run(repository.set_status(trip=trip, status=status))

    assert result is trip
    assert trip.status == status.value
    assert isinstance(trip.status, str)
    assert trip.updated_at == updated_at
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
