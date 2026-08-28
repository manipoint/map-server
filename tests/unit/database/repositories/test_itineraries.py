"""Tests for itinerary persistence operations."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Itinerary, ItineraryItem, Trip
from app.database.repositories.itineraries import (
    ItineraryDetails,
    ItineraryRepository,
)
from app.domain.itineraries import (
    ItineraryItemDraft,
    ItineraryItemType,
    ItineraryStatus,
)


def create_mock_session() -> Mock:
    """Create an asynchronous database-session mock."""

    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def create_scalar_result(value: object) -> Mock:
    """Create a query result exposing one scalar value."""

    result = Mock()
    result.scalar_one.return_value = value
    result.scalar_one_or_none.return_value = value
    return result


def create_scalars_result(values: list[object]) -> Mock:
    """Create a query result exposing a scalar collection."""

    scalars = Mock()
    scalars.all.return_value = values
    result = Mock()
    result.scalars.return_value = scalars
    return result


def test_create_next_draft_locks_owner_and_flushes_next_version() -> None:
    """Owned creation should serialize versions without committing."""

    trip_id = uuid4()
    user_id = uuid4()
    trip = Trip(id=trip_id, user_id=user_id)
    session = create_mock_session()
    session.execute.side_effect = [
        create_scalar_result(trip),
        create_scalar_result(3),
    ]
    repository = ItineraryRepository(session)

    itinerary = asyncio.run(
        repository.create_next_draft_for_user(
            trip_id=trip_id,
            user_id=user_id,
        )
    )

    assert isinstance(itinerary, Itinerary)
    assert itinerary.trip_id == trip_id
    assert itinerary.version == 3
    assert itinerary.status == ItineraryStatus.DRAFT.value
    session.add.assert_called_once_with(itinerary)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()

    ownership_statement = session.execute.await_args_list[0].args[0]
    ownership_compiled = ownership_statement.compile()
    assert trip_id in ownership_compiled.params.values()
    assert user_id in ownership_compiled.params.values()
    assert "trips.id" in str(ownership_compiled)
    assert "trips.user_id" in str(ownership_compiled)
    assert "FOR UPDATE" in str(ownership_compiled)


def test_create_next_draft_uses_max_version_for_owned_trip() -> None:
    """Version allocation should inspect only the locked trip's versions."""

    trip_id = uuid4()
    trip = Trip(id=trip_id, user_id=uuid4())
    session = create_mock_session()
    session.execute.side_effect = [
        create_scalar_result(trip),
        create_scalar_result(1),
    ]
    repository = ItineraryRepository(session)

    itinerary = asyncio.run(
        repository.create_next_draft_for_user(
            trip_id=trip_id,
            user_id=uuid4(),
        )
    )

    assert itinerary is not None
    assert itinerary.version == 1

    version_statement = session.execute.await_args_list[1].args[0]
    version_compiled = version_statement.compile()
    version_sql = str(version_compiled)
    assert trip_id in version_compiled.params.values()
    assert 0 in version_compiled.params.values()
    assert 1 in version_compiled.params.values()
    assert "max(app.itineraries.version)" in version_sql
    assert "itineraries.trip_id" in version_sql


def test_create_next_draft_for_locked_trip_stores_source_message() -> None:
    """Generated drafts should retain their idempotency source message."""

    trip = Trip(id=uuid4(), user_id=uuid4())
    source_message_id = uuid4()
    session = create_mock_session()
    session.execute.return_value = create_scalar_result(2)
    repository = ItineraryRepository(session)

    itinerary = asyncio.run(
        repository.create_next_draft_for_locked_trip(
            trip=trip,
            source_message_id=source_message_id,
        )
    )

    assert itinerary.trip_id == trip.id
    assert itinerary.source_message_id == source_message_id
    assert itinerary.version == 2
    session.add.assert_called_once_with(itinerary)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_create_next_draft_hides_missing_or_differently_owned_trip() -> None:
    """A failed ownership lookup should not allocate or create a version."""

    session = create_mock_session()
    session.execute.return_value = create_scalar_result(None)
    repository = ItineraryRepository(session)

    itinerary = asyncio.run(
        repository.create_next_draft_for_user(
            trip_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert itinerary is None
    session.execute.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_add_items_maps_validated_fields_and_flushes_without_commit() -> None:
    """Bulk insertion should preserve ordering, types, display fields, and times."""

    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=1,
        status=ItineraryStatus.DRAFT.value,
    )
    starts_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    drafts = (
        ItineraryItemDraft(
            day_number=1,
            position=1,
            item_type=ItineraryItemType.PLACE,
            title="British Museum",
            description="Explore the galleries",
            location_name="London",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
        ),
        ItineraryItemDraft(
            day_number=1,
            position=2,
            item_type=ItineraryItemType.NOTE,
            title="Remember passport",
        ),
    )
    session = create_mock_session()
    repository = ItineraryRepository(session)

    items = asyncio.run(repository.add_items(itinerary=itinerary, items=drafts))

    assert len(items) == 2
    assert all(isinstance(item, ItineraryItem) for item in items)

    first_item, second_item = items
    assert first_item.itinerary_id == itinerary.id
    assert first_item.day_number == 1
    assert first_item.position == 1
    assert first_item.item_type == ItineraryItemType.PLACE.value
    assert first_item.title == "British Museum"
    assert first_item.description == "Explore the galleries"
    assert first_item.location_name == "London"
    assert first_item.starts_at == starts_at
    assert first_item.ends_at == starts_at + timedelta(hours=2)

    assert second_item.itinerary_id == itinerary.id
    assert second_item.position == 2
    assert second_item.item_type == ItineraryItemType.NOTE.value
    assert second_item.description is None
    assert second_item.location_name is None
    assert second_item.starts_at is None
    assert second_item.ends_at is None

    session.add_all.assert_called_once_with(items)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_add_items_returns_empty_without_database_work() -> None:
    """An empty item collection should remain a database no-op."""

    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=1,
        status=ItineraryStatus.DRAFT.value,
    )
    session = create_mock_session()
    repository = ItineraryRepository(session)

    items = asyncio.run(repository.add_items(itinerary=itinerary, items=[]))

    assert items == []
    session.add_all.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(("stored_id", "expected"), [(uuid4(), True), (None, False)])
def test_has_items_uses_an_existence_query(
    stored_id: object,
    expected: bool,
) -> None:
    """Item existence should be checked without loading the timeline."""

    itinerary_id = uuid4()
    session = create_mock_session()
    session.execute.return_value = create_scalar_result(stored_id)
    repository = ItineraryRepository(session)

    result = asyncio.run(repository.has_items(itinerary_id=itinerary_id))

    assert result is expected
    statement = session.execute.await_args.args[0]
    compiled = statement.compile()
    assert itinerary_id in compiled.params.values()
    assert "itinerary_items.itinerary_id" in str(compiled)
    assert "LIMIT" in str(compiled)


def test_get_details_enforces_owner_and_returns_ordered_items() -> None:
    """Owned detail retrieval should return the itinerary and timeline rows."""

    user_id = uuid4()
    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=1,
        status=ItineraryStatus.SAVED.value,
    )
    items = [Mock(spec=ItineraryItem), Mock(spec=ItineraryItem)]
    session = create_mock_session()
    session.execute.side_effect = [
        create_scalar_result(itinerary),
        create_scalars_result(items),
    ]
    repository = ItineraryRepository(session)

    details = asyncio.run(
        repository.get_details_for_user(
            itinerary_id=itinerary.id,
            user_id=user_id,
        )
    )

    assert details == ItineraryDetails(itinerary=itinerary, items=items)

    ownership_statement = session.execute.await_args_list[0].args[0]
    ownership_compiled = ownership_statement.compile()
    ownership_sql = str(ownership_compiled)
    assert itinerary.id in ownership_compiled.params.values()
    assert user_id in ownership_compiled.params.values()
    assert "JOIN app.trips" in ownership_sql
    assert "trips.user_id" in ownership_sql

    items_statement = session.execute.await_args_list[1].args[0]
    items_compiled = items_statement.compile()
    items_sql = str(items_compiled)
    assert itinerary.id in items_compiled.params.values()
    assert (
        "ORDER BY app.itinerary_items.day_number ASC, "
        "app.itinerary_items.position ASC, app.itinerary_items.id ASC"
    ) in items_sql
    session.commit.assert_not_awaited()


def test_get_details_hides_missing_or_differently_owned_itinerary() -> None:
    """A failed ownership lookup should not query or expose timeline items."""

    session = create_mock_session()
    session.execute.return_value = create_scalar_result(None)
    repository = ItineraryRepository(session)

    details = asyncio.run(
        repository.get_details_for_user(
            itinerary_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert details is None
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_get_details_returns_owned_itinerary_with_empty_timeline() -> None:
    """An owned draft may legitimately exist before items are added."""

    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=1,
        status=ItineraryStatus.DRAFT.value,
    )
    session = create_mock_session()
    session.execute.side_effect = [
        create_scalar_result(itinerary),
        create_scalars_result([]),
    ]
    repository = ItineraryRepository(session)

    details = asyncio.run(
        repository.get_details_for_user(
            itinerary_id=itinerary.id,
            user_id=uuid4(),
        )
    )

    assert details == ItineraryDetails(itinerary=itinerary, items=[])
    assert session.execute.await_count == 2


def test_get_details_by_source_message_enforces_owner_and_loads_items() -> None:
    """Source lookup should return only an owned itinerary and its timeline."""

    source_message_id = uuid4()
    user_id = uuid4()
    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        source_message_id=source_message_id,
        version=2,
        status=ItineraryStatus.DRAFT.value,
    )
    items = [Mock(spec=ItineraryItem)]
    session = create_mock_session()
    session.execute.side_effect = [
        create_scalar_result(itinerary),
        create_scalars_result(items),
    ]
    repository = ItineraryRepository(session)

    details = asyncio.run(
        repository.get_details_by_source_message(
            source_message_id=source_message_id,
            user_id=user_id,
        )
    )

    assert details == ItineraryDetails(itinerary=itinerary, items=items)
    statement = session.execute.await_args_list[0].kwargs["statement"]
    compiled = statement.compile()
    compiled_sql = str(compiled)
    assert source_message_id in compiled.params.values()
    assert user_id in compiled.params.values()
    assert "JOIN app.trips" in compiled_sql
    assert "itineraries.source_message_id" in compiled_sql
    assert "trips.user_id" in compiled_sql
    assert session.execute.await_count == 2


def test_get_details_by_source_message_hides_missing_or_unowned_result() -> None:
    """A missing owned source should not trigger an item query."""

    session = create_mock_session()
    session.execute.return_value = create_scalar_result(None)
    repository = ItineraryRepository(session)

    details = asyncio.run(
        repository.get_details_by_source_message(
            source_message_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert details is None
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_get_saved_details_filters_trip_owner_and_saved_status() -> None:
    """Current-plan retrieval should require trip ownership and saved status."""

    trip_id = uuid4()
    user_id = uuid4()
    itinerary = Itinerary(
        id=uuid4(),
        trip_id=trip_id,
        version=2,
        status=ItineraryStatus.SAVED.value,
    )
    items = [Mock(spec=ItineraryItem)]
    session = create_mock_session()
    session.execute.side_effect = [
        create_scalar_result(itinerary),
        create_scalars_result(items),
    ]
    repository = ItineraryRepository(session)

    details = asyncio.run(
        repository.get_saved_details_for_trip_user(
            trip_id=trip_id,
            user_id=user_id,
        )
    )

    assert details == ItineraryDetails(itinerary=itinerary, items=items)

    statement = session.execute.await_args_list[0].args[0]
    compiled_statement = statement.compile()
    compiled_sql = str(compiled_statement)
    assert trip_id in compiled_statement.params.values()
    assert user_id in compiled_statement.params.values()
    assert ItineraryStatus.SAVED.value in compiled_statement.params.values()
    assert "JOIN app.trips" in compiled_sql
    assert "itineraries.trip_id" in compiled_sql
    assert "itineraries.status" in compiled_sql
    assert "trips.user_id" in compiled_sql
    assert session.execute.await_count == 2
    session.commit.assert_not_awaited()


def test_get_saved_details_hides_no_saved_or_differently_owned_trip() -> None:
    """No owned saved version should return none without querying items."""

    session = create_mock_session()
    session.execute.return_value = create_scalar_result(None)
    repository = ItineraryRepository(session)

    details = asyncio.run(
        repository.get_saved_details_for_trip_user(
            trip_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert details is None
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_get_for_user_with_trip_lock_locks_owned_parent_trip() -> None:
    """Save preparation should serialize through the owned parent trip row."""

    itinerary_id = uuid4()
    trip_id = uuid4()
    user_id = uuid4()
    itinerary = Itinerary(
        id=itinerary_id,
        trip_id=trip_id,
        version=2,
        status=ItineraryStatus.DRAFT.value,
    )
    session = create_mock_session()
    session.execute.return_value = create_scalar_result(itinerary)
    repository = ItineraryRepository(session)

    result = asyncio.run(
        repository.get_for_user_with_trip_lock(
            itinerary_id=itinerary_id,
            user_id=user_id,
        )
    )

    assert result is itinerary
    statement = session.execute.await_args.args[0]
    compiled_statement = statement.compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled_statement)
    assert itinerary_id in compiled_statement.params.values()
    assert user_id in compiled_statement.params.values()
    assert "JOIN app.trips" in compiled_sql
    assert "trips.user_id" in compiled_sql
    assert "FOR UPDATE OF trips" in compiled_sql
    session.commit.assert_not_awaited()


def test_get_for_user_with_trip_lock_hides_missing_or_unowned_itinerary() -> None:
    """Missing ownership should return none without mutating the transaction."""

    session = create_mock_session()
    session.execute.return_value = create_scalar_result(None)
    repository = ItineraryRepository(session)

    result = asyncio.run(
        repository.get_for_user_with_trip_lock(
            itinerary_id=uuid4(),
            user_id=uuid4(),
        )
    )

    assert result is None
    session.execute.assert_awaited_once()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_replace_saved_with_supersedes_other_version_and_flushes_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacement should update the old saved row before flushing the target."""

    updated_at = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.database.repositories.itineraries.utc_now",
        lambda: updated_at,
    )
    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=2,
        status=ItineraryStatus.DRAFT.value,
    )
    session = create_mock_session()
    repository = ItineraryRepository(session)

    result = asyncio.run(repository.replace_saved_with(itinerary=itinerary))

    assert result is itinerary
    assert itinerary.status == ItineraryStatus.SAVED.value
    assert itinerary.updated_at == updated_at

    statement = session.execute.await_args.args[0]
    compiled_statement = statement.compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled_statement)
    assert statement.is_update
    assert itinerary.trip_id in compiled_statement.params.values()
    assert itinerary.id in compiled_statement.params.values()
    assert ItineraryStatus.SAVED.value in compiled_statement.params.values()
    assert ItineraryStatus.SUPERSEDED.value in compiled_statement.params.values()
    assert updated_at in compiled_statement.params.values()
    assert "itineraries.id !=" in compiled_sql
    assert "itineraries.status =" in compiled_sql
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


def test_replace_saved_with_propagates_update_failure_without_flush() -> None:
    """A failed supersede update should leave rollback to the service."""

    itinerary = Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=2,
        status=ItineraryStatus.DRAFT.value,
    )
    session = create_mock_session()
    session.execute.side_effect = RuntimeError("supersede failed")
    repository = ItineraryRepository(session)

    with pytest.raises(RuntimeError, match="supersede failed"):
        asyncio.run(repository.replace_saved_with(itinerary=itinerary))

    assert itinerary.status == ItineraryStatus.DRAFT.value
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
