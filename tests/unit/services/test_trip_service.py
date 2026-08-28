"""Tests for trip management use cases."""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import InvalidCursorError
from app.common.pagination import PageCursor, decode_page_cursor, encode_page_cursor
from app.database.models.trip import Trip
from app.database.repositories.trips import TripPage, TripRepository
from app.domain.errors import (
    InvalidTripDetailsError,
    InvalidTripStatusTransitionError,
    TripNotFoundError,
)
from app.domain.trips import CanonicalLocation, TripStatus, TripUpdate
from app.services.trip_service import TripListResult, TripService


def create_dependencies() -> tuple[Mock, Mock]:
    """Create mocked service persistence dependencies."""

    session = Mock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    repository = Mock(spec=TripRepository)
    repository.create = AsyncMock()
    repository.get_by_id_for_user = AsyncMock()
    repository.list_for_user = AsyncMock()
    repository.delete_by_id_for_user = AsyncMock()
    repository.set_status = AsyncMock()
    repository.update_details = AsyncMock()
    return session, repository


def create_stored_trip() -> Trip:
    """Create a persisted-shape trip for service mutation tests."""

    return Trip(
        id=uuid4(),
        user_id=uuid4(),
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        status=TripStatus.DRAFT.value,
        updated_at=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
    )


def test_list_trips_is_available_on_service_instance() -> None:
    """The listing use case must not be nested inside the constructor."""

    session, repository = create_dependencies()
    service = TripService(session=session, trip_repository=repository)

    assert callable(service.list_trips)


def test_list_trips_without_cursor_returns_final_page() -> None:
    """The first and final page should not manufacture a next cursor."""

    user_id = uuid4()
    trip = Mock(spec=Trip)
    session, repository = create_dependencies()
    repository.list_for_user.return_value = TripPage(items=[trip], has_more=False)
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(service.list_trips(user_id=user_id))

    assert result == TripListResult(items=[trip], next_cursor=None)
    repository.list_for_user.assert_awaited_once_with(
        user_id=user_id,
        status=None,
        limit=20,
        before_updated_at=None,
        before_id=None,
    )
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_list_trips_decodes_cursor_and_forwards_filters() -> None:
    """An opaque cursor should become repository keyset values."""

    user_id = uuid4()
    page_cursor = PageCursor(
        updated_at=datetime(2026, 8, 26, 12, 30, tzinfo=UTC),
        item_id=uuid4(),
    )
    encoded_cursor = encode_page_cursor(page_cursor)
    session, repository = create_dependencies()
    repository.list_for_user.return_value = TripPage(items=[], has_more=False)
    service = TripService(session=session, trip_repository=repository)

    asyncio.run(
        service.list_trips(
            user_id=user_id,
            status=TripStatus.PLANNED,
            limit=10,
            cursor=encoded_cursor,
        )
    )

    repository.list_for_user.assert_awaited_once_with(
        user_id=user_id,
        status=TripStatus.PLANNED,
        limit=10,
        before_updated_at=page_cursor.updated_at,
        before_id=page_cursor.item_id,
    )


def test_list_trips_encodes_last_visible_item_when_more_exist() -> None:
    """A non-final page should expose its last visible row as next cursor."""

    first_trip = Mock(spec=Trip)
    first_trip.id = uuid4()
    first_trip.updated_at = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
    last_trip = Mock(spec=Trip)
    last_trip.id = uuid4()
    last_trip.updated_at = datetime(2026, 8, 25, 10, 15, tzinfo=UTC)
    session, repository = create_dependencies()
    repository.list_for_user.return_value = TripPage(
        items=[first_trip, last_trip],
        has_more=True,
    )
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(service.list_trips(user_id=uuid4(), limit=2))

    assert result.next_cursor is not None
    assert decode_page_cursor(result.next_cursor) == PageCursor(
        updated_at=last_trip.updated_at,
        item_id=last_trip.id,
    )


def test_list_trips_rejects_invalid_cursor_before_database_query() -> None:
    """Malformed cursors should fail without spending a database call."""

    session, repository = create_dependencies()
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(InvalidCursorError, match="Pagination cursor is invalid"):
        asyncio.run(service.list_trips(user_id=uuid4(), cursor="invalid%%%"))

    repository.list_for_user.assert_not_awaited()


def test_create_trip_commits_repository_result() -> None:
    """A successfully flushed trip should be committed and returned."""

    user_id = uuid4()
    trip = Mock(spec=Trip)
    session, repository = create_dependencies()
    repository.create.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(
        service.create_trip(
            user_id=user_id,
            title="London museums",
            origin="Lahore",
            destination="London",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
        )
    )

    assert result is trip
    repository.create.assert_awaited_once_with(
        user_id=user_id,
        title="London museums",
        origin="Lahore",
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        origin_location=None,
        destination_location=None,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_update_trip_clears_stale_location_when_endpoint_text_changes() -> None:
    """Changing free text without a new resolution must discard old metadata."""

    trip = create_stored_trip()
    trip.destination_location_provider = "google"
    trip.destination_provider_location_id = "london-id"
    trip.destination_canonical_name = "London, United Kingdom"
    trip.destination_country_code = "GB"
    trip.destination_latitude = 51.5074
    trip.destination_longitude = -0.1278
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    repository.update_details.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    asyncio.run(
        service.update_trip(
            trip_id=trip.id,
            user_id=trip.user_id,
            update=TripUpdate(destination="Paris"),
        )
    )

    assert repository.update_details.await_args.kwargs["destination_location"] is None


def test_update_trip_accepts_replacement_canonical_location() -> None:
    """A location selection should replace text and metadata atomically."""

    trip = create_stored_trip()
    location = CanonicalLocation(
        provider="google",
        provider_location_id="paris-id",
        canonical_name="Paris, France",
        country_code="fr",
        latitude=48.8566,
        longitude=2.3522,
    )
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    repository.update_details.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    asyncio.run(
        service.update_trip(
            trip_id=trip.id,
            user_id=trip.user_id,
            update=TripUpdate(
                destination="Paris",
                destination_location=location,
            ),
        )
    )

    assert (
        repository.update_details.await_args.kwargs["destination_location"] == location
    )


def test_update_trip_preserves_location_when_endpoint_text_is_unchanged() -> None:
    """Idempotent Flutter PATCH payloads must retain resolved metadata."""

    trip = create_stored_trip()
    trip.destination_location_provider = "google"
    trip.destination_provider_location_id = "london-id"
    trip.destination_canonical_name = "London, United Kingdom"
    trip.destination_country_code = "GB"
    trip.destination_latitude = 51.5074
    trip.destination_longitude = -0.1278
    stored_location = trip.destination_location
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    repository.update_details.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    asyncio.run(
        service.update_trip(
            trip_id=trip.id,
            user_id=trip.user_id,
            update=TripUpdate(destination="london"),
        )
    )

    assert (
        repository.update_details.await_args.kwargs["destination_location"]
        == stored_location
    )


def test_create_trip_rolls_back_repository_failure() -> None:
    """A failed insert should roll back before propagating its error."""

    session, repository = create_dependencies()
    repository.create.side_effect = RuntimeError("database write failed")
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(RuntimeError, match="database write failed"):
        asyncio.run(
            service.create_trip(
                user_id=uuid4(),
                destination="London",
                start_date=date(2026, 9, 10),
                end_date=date(2026, 9, 12),
            )
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_create_trip_rolls_back_commit_failure() -> None:
    """A failed commit should leave the session ready for later requests."""

    session, repository = create_dependencies()
    repository.create.return_value = Mock(spec=Trip)
    session.commit.side_effect = RuntimeError("commit failed")
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(
            service.create_trip(
                user_id=uuid4(),
                destination="London",
                start_date=date(2026, 9, 10),
                end_date=date(2026, 9, 12),
            )
        )

    session.commit.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()


def test_get_trip_returns_user_owned_trip_without_transaction_write() -> None:
    """A matching trip should be returned without committing or rolling back."""

    trip_id = uuid4()
    user_id = uuid4()
    trip = Mock(spec=Trip)
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(service.get_trip(trip_id=trip_id, user_id=user_id))

    assert result is trip
    repository.get_by_id_for_user.assert_awaited_once_with(
        trip_id=trip_id,
        user_id=user_id,
    )
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_get_trip_hides_missing_or_differently_owned_trip() -> None:
    """Missing and differently owned trips should share one safe error."""

    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = None
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(TripNotFoundError, match="Trip was not found"):
        asyncio.run(service.get_trip(trip_id=uuid4(), user_id=uuid4()))

    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_update_trip_locks_merges_commits_and_returns_trip() -> None:
    """A valid partial update should preserve omitted fields and clear nullables."""

    trip = create_stored_trip()
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    repository.update_details.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(
        service.update_trip(
            trip_id=trip.id,
            user_id=trip.user_id,
            update=TripUpdate(title=None, destination="Paris"),
        )
    )

    assert result is trip
    repository.get_by_id_for_user.assert_awaited_once_with(
        trip_id=trip.id,
        user_id=trip.user_id,
        for_update=True,
    )
    repository.update_details.assert_awaited_once_with(
        trip=trip,
        title=None,
        origin="Lahore",
        destination="Paris",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        origin_location=None,
        destination_location=None,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_update_trip_hides_missing_or_differently_owned_trip() -> None:
    """A failed locked owner lookup should return the safe not-found error."""

    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = None
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(TripNotFoundError, match="Trip was not found"):
        asyncio.run(
            service.update_trip(
                trip_id=uuid4(),
                user_id=uuid4(),
                update=TripUpdate(title="Updated title"),
            )
        )

    repository.update_details.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_update_trip_rejects_invalid_merged_date_range() -> None:
    """A single changed date should be checked against the stored counterpart."""

    trip = create_stored_trip()
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(
        InvalidTripDetailsError,
        match="end_date must be after start_date",
    ):
        asyncio.run(
            service.update_trip(
                trip_id=trip.id,
                user_id=trip.user_id,
                update=TripUpdate(end_date=date(2026, 9, 9)),
            )
        )

    repository.update_details.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_update_trip_rejects_equal_merged_origin_and_destination() -> None:
    """Changing one route endpoint should still validate the complete route."""

    trip = create_stored_trip()
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(
        InvalidTripDetailsError,
        match="origin and destination must be different",
    ):
        asyncio.run(
            service.update_trip(
                trip_id=trip.id,
                user_id=trip.user_id,
                update=TripUpdate(destination="lahore"),
            )
        )

    repository.update_details.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_update_trip_rolls_back_repository_failure() -> None:
    """A failed detail flush should roll back before propagating the error."""

    trip = create_stored_trip()
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    repository.update_details.side_effect = RuntimeError("update failed")
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(RuntimeError, match="update failed"):
        asyncio.run(
            service.update_trip(
                trip_id=trip.id,
                user_id=trip.user_id,
                update=TripUpdate(title="Updated title"),
            )
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_mark_trip_planned_locks_draft_and_commits_transition() -> None:
    """A draft should transition to planned through the locked repository row."""

    trip = create_stored_trip()
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    repository.set_status.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(
        service.mark_trip_planned(trip_id=trip.id, user_id=trip.user_id)
    )

    assert result is trip
    repository.get_by_id_for_user.assert_awaited_once_with(
        trip_id=trip.id,
        user_id=trip.user_id,
        for_update=True,
    )
    repository.set_status.assert_awaited_once_with(
        trip=trip,
        status=TripStatus.PLANNED,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("source_status", [TripStatus.DRAFT, TripStatus.PLANNED])
def test_archive_trip_accepts_draft_or_planned_status(
    source_status: TripStatus,
) -> None:
    """Both editable lifecycle states should be archivable."""

    trip = create_stored_trip()
    trip.status = source_status.value
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    repository.set_status.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(service.archive_trip(trip_id=trip.id, user_id=trip.user_id))

    assert result is trip
    repository.set_status.assert_awaited_once_with(
        trip=trip,
        status=TripStatus.ARCHIVED,
    )
    session.commit.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("status", "operation"),
    [
        (TripStatus.PLANNED, "planned"),
        (TripStatus.ARCHIVED, "archived"),
    ],
)
def test_status_transition_is_idempotent_at_target(
    status: TripStatus,
    operation: str,
) -> None:
    """Repeating a successful transition should not write another update."""

    trip = create_stored_trip()
    trip.status = status.value
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    if operation == "planned":
        result = asyncio.run(
            service.mark_trip_planned(trip_id=trip.id, user_id=trip.user_id)
        )
    else:
        result = asyncio.run(
            service.archive_trip(trip_id=trip.id, user_id=trip.user_id)
        )

    assert result is trip
    repository.set_status.assert_not_awaited()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_mark_trip_planned_rejects_archived_trip() -> None:
    """An archived trip should not silently return to an active lifecycle."""

    trip = create_stored_trip()
    trip.status = TripStatus.ARCHIVED.value
    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = trip
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(
        InvalidTripStatusTransitionError,
        match="Trip cannot transition from archived to planned",
    ):
        asyncio.run(service.mark_trip_planned(trip_id=trip.id, user_id=trip.user_id))

    repository.set_status.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_status_transition_hides_missing_or_differently_owned_trip() -> None:
    """Lifecycle actions should not reveal another user's trip existence."""

    session, repository = create_dependencies()
    repository.get_by_id_for_user.return_value = None
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(TripNotFoundError, match="Trip was not found"):
        asyncio.run(service.archive_trip(trip_id=uuid4(), user_id=uuid4()))

    repository.set_status.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_delete_trip_commits_successful_owned_deletion() -> None:
    """A deleted owned row should commit exactly once."""

    trip_id = uuid4()
    user_id = uuid4()
    session, repository = create_dependencies()
    repository.delete_by_id_for_user.return_value = True
    service = TripService(session=session, trip_repository=repository)

    result = asyncio.run(service.delete_trip(trip_id=trip_id, user_id=user_id))

    assert result is None
    repository.delete_by_id_for_user.assert_awaited_once_with(
        trip_id=trip_id,
        user_id=user_id,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_delete_trip_hides_missing_or_differently_owned_trip() -> None:
    """No deleted row should produce the same safe not-found error."""

    session, repository = create_dependencies()
    repository.delete_by_id_for_user.return_value = False
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(TripNotFoundError, match="Trip was not found"):
        asyncio.run(service.delete_trip(trip_id=uuid4(), user_id=uuid4()))

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_delete_trip_rolls_back_repository_failure() -> None:
    """A failed DELETE statement should roll back before propagating."""

    session, repository = create_dependencies()
    repository.delete_by_id_for_user.side_effect = RuntimeError("delete failed")
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(RuntimeError, match="delete failed"):
        asyncio.run(service.delete_trip(trip_id=uuid4(), user_id=uuid4()))

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_delete_trip_rolls_back_cancellation() -> None:
    """Task cancellation must not leave the transaction open."""

    session, repository = create_dependencies()
    repository.delete_by_id_for_user.side_effect = asyncio.CancelledError()
    service = TripService(session=session, trip_repository=repository)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.delete_trip(trip_id=uuid4(), user_id=uuid4()))

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
