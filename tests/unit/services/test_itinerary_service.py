"""Tests for versioned itinerary use cases."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Itinerary, ItineraryItem, Trip
from app.database.repositories.itineraries import (
    ItineraryDetails,
    ItineraryRepository,
)
from app.domain.errors import (
    InvalidItineraryDetailsError,
    InvalidItineraryStatusTransitionError,
    ItineraryNotFoundError,
    TripNotFoundError,
)
from app.domain.itineraries import (
    ItineraryItemDraft,
    ItineraryItemType,
    ItineraryStatus,
)
from app.services.itinerary_service import ItineraryService


def create_service() -> tuple[ItineraryService, AsyncMock, MagicMock]:
    """Create a service with isolated session and repository mocks."""

    session = AsyncMock(spec=AsyncSession)
    repository = MagicMock(spec=ItineraryRepository)
    repository.get_owned_trip_with_lock = AsyncMock()
    repository.create_next_draft_for_locked_trip = AsyncMock()
    repository.add_items = AsyncMock()
    repository.get_details_for_user = AsyncMock()
    repository.get_saved_details_for_trip_user = AsyncMock()
    repository.get_for_user_with_trip_lock = AsyncMock()
    repository.get_details_by_source_message = AsyncMock(return_value=None)
    repository.has_items = AsyncMock()
    repository.replace_saved_with = AsyncMock()
    return (
        ItineraryService(
            session=session,
            itinerary_repository=repository,
        ),
        session,
        repository,
    )


def create_item(*, day: int = 1, position: int = 1) -> ItineraryItemDraft:
    """Create one valid itinerary input item."""

    return ItineraryItemDraft(
        day_number=day,
        position=position,
        item_type=ItineraryItemType.PLACE,
        title="British Museum",
    )


def create_trip() -> Trip:
    """Create a three-day owned trip."""

    return Trip(
        id=uuid4(),
        user_id=uuid4(),
        destination="London",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        status="draft",
    )


def create_itinerary(*, status: ItineraryStatus) -> Itinerary:
    """Create one itinerary model in the requested state."""

    return Itinerary(
        id=uuid4(),
        trip_id=uuid4(),
        version=1,
        status=status.value,
    )


def test_create_draft_validates_and_commits_complete_version() -> None:
    """A valid timeline should be persisted atomically as the next version."""

    service, session, repository = create_service()
    trip = create_trip()
    itinerary = create_itinerary(status=ItineraryStatus.DRAFT)
    item_drafts = [create_item(), create_item(day=2)]
    created_items = [MagicMock(spec=ItineraryItem), MagicMock(spec=ItineraryItem)]
    repository.get_owned_trip_with_lock.return_value = trip
    repository.create_next_draft_for_locked_trip.return_value = itinerary
    repository.add_items.return_value = created_items

    result = asyncio.run(
        service.create_draft(
            trip_id=trip.id,
            user_id=trip.user_id,
            items=item_drafts,
        )
    )

    assert result == ItineraryDetails(itinerary=itinerary, items=created_items)
    repository.create_next_draft_for_locked_trip.assert_awaited_once_with(trip=trip)
    repository.add_items.assert_awaited_once_with(
        itinerary=itinerary,
        items=item_drafts,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_create_draft_hides_unowned_trip_and_rolls_back() -> None:
    """Missing ownership should fail safely before creating a version."""

    service, session, repository = create_service()
    repository.get_owned_trip_with_lock.return_value = None

    with pytest.raises(TripNotFoundError):
        asyncio.run(
            service.create_draft(
                trip_id=uuid4(),
                user_id=uuid4(),
                items=[create_item()],
            )
        )

    repository.create_next_draft_for_locked_trip.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


@pytest.mark.parametrize(
    "items",
    [
        [],
        [create_item(), create_item()],
    ],
)
def test_create_draft_rejects_invalid_timeline_before_database_work(
    items: list[ItineraryItemDraft],
) -> None:
    """Empty timelines and duplicate day positions should be rejected early."""

    service, session, repository = create_service()

    with pytest.raises(InvalidItineraryDetailsError):
        asyncio.run(
            service.create_draft(
                trip_id=uuid4(),
                user_id=uuid4(),
                items=items,
            )
        )

    repository.get_owned_trip_with_lock.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_create_draft_rejects_item_outside_trip_duration() -> None:
    """A day number cannot exceed the inclusive trip calendar duration."""

    service, session, repository = create_service()
    repository.get_owned_trip_with_lock.return_value = create_trip()

    with pytest.raises(InvalidItineraryDetailsError, match="date range"):
        asyncio.run(
            service.create_draft(
                trip_id=uuid4(),
                user_id=uuid4(),
                items=[create_item(day=4)],
            )
        )

    repository.create_next_draft_for_locked_trip.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_create_generated_draft_reuses_existing_source_without_locking() -> None:
    """A completed retry should return its existing draft immediately."""

    service, session, repository = create_service()
    existing = MagicMock(spec=ItineraryDetails)
    source_message_id = uuid4()
    user_id = uuid4()
    repository.get_details_by_source_message.return_value = existing

    result = asyncio.run(
        service.create_generated_draft(
            trip_id=uuid4(),
            user_id=user_id,
            source_message_id=source_message_id,
            items=[create_item()],
        )
    )

    assert result is existing
    repository.get_details_by_source_message.assert_awaited_once_with(
        source_message_id=source_message_id,
        user_id=user_id,
    )
    repository.get_owned_trip_with_lock.assert_not_awaited()
    repository.create_next_draft_for_locked_trip.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_create_generated_draft_rechecks_source_after_trip_lock() -> None:
    """A worker waiting on the trip lock should reuse the winning draft."""

    service, session, repository = create_service()
    trip = create_trip()
    existing = MagicMock(spec=ItineraryDetails)
    source_message_id = uuid4()
    repository.get_details_by_source_message.side_effect = [None, existing]
    repository.get_owned_trip_with_lock.return_value = trip

    result = asyncio.run(
        service.create_generated_draft(
            trip_id=trip.id,
            user_id=trip.user_id,
            source_message_id=source_message_id,
            items=[create_item()],
        )
    )

    assert result is existing
    assert repository.get_details_by_source_message.await_count == 2
    repository.create_next_draft_for_locked_trip.assert_not_awaited()
    repository.add_items.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_create_generated_draft_persists_one_new_source_version() -> None:
    """A new source should create and commit one complete draft version."""

    service, session, repository = create_service()
    trip = create_trip()
    source_message_id = uuid4()
    itinerary = create_itinerary(status=ItineraryStatus.DRAFT)
    items = [create_item(), create_item(day=2)]
    created_items = [MagicMock(spec=ItineraryItem), MagicMock(spec=ItineraryItem)]
    repository.get_details_by_source_message.side_effect = [None, None]
    repository.get_owned_trip_with_lock.return_value = trip
    repository.create_next_draft_for_locked_trip.return_value = itinerary
    repository.add_items.return_value = created_items

    result = asyncio.run(
        service.create_generated_draft(
            trip_id=trip.id,
            user_id=trip.user_id,
            source_message_id=source_message_id,
            items=items,
        )
    )

    assert result == ItineraryDetails(itinerary=itinerary, items=created_items)
    repository.create_next_draft_for_locked_trip.assert_awaited_once_with(
        trip=trip,
        source_message_id=source_message_id,
    )
    repository.add_items.assert_awaited_once_with(
        itinerary=itinerary,
        items=items,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_create_generated_draft_rejects_unowned_trip_and_rolls_back() -> None:
    """A generated draft must not bypass trip ownership."""

    service, session, repository = create_service()
    repository.get_owned_trip_with_lock.return_value = None

    with pytest.raises(TripNotFoundError):
        asyncio.run(
            service.create_generated_draft(
                trip_id=uuid4(),
                user_id=uuid4(),
                source_message_id=uuid4(),
                items=[create_item()],
            )
        )

    repository.create_next_draft_for_locked_trip.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


def test_create_generated_draft_rolls_back_commit_failure() -> None:
    """A failed generated-draft commit should leave the session reusable."""

    service, session, repository = create_service()
    trip = create_trip()
    repository.get_owned_trip_with_lock.return_value = trip
    repository.create_next_draft_for_locked_trip.return_value = create_itinerary(
        status=ItineraryStatus.DRAFT
    )
    repository.add_items.return_value = [MagicMock(spec=ItineraryItem)]
    session.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(
            service.create_generated_draft(
                trip_id=trip.id,
                user_id=trip.user_id,
                source_message_id=uuid4(),
                items=[create_item()],
            )
        )

    session.rollback.assert_awaited_once_with()


def test_get_itinerary_returns_owned_details() -> None:
    """Owned detail retrieval should pass through the repository result."""

    service, _, repository = create_service()
    details = MagicMock(spec=ItineraryDetails)
    repository.get_details_for_user.return_value = details
    itinerary_id = uuid4()
    user_id = uuid4()

    result = asyncio.run(
        service.get_itinerary(itinerary_id=itinerary_id, user_id=user_id)
    )

    assert result is details
    repository.get_details_for_user.assert_awaited_once_with(
        itinerary_id=itinerary_id,
        user_id=user_id,
    )


def test_get_itinerary_raises_safe_not_found() -> None:
    """Missing and unowned itinerary IDs should share a safe failure."""

    service, _, repository = create_service()
    repository.get_details_for_user.return_value = None

    with pytest.raises(ItineraryNotFoundError):
        asyncio.run(service.get_itinerary(itinerary_id=uuid4(), user_id=uuid4()))


def test_find_generated_draft_returns_optional_source_details() -> None:
    """Generated lookup should preserve absence as a normal result."""

    service, _, repository = create_service()
    details = MagicMock(spec=ItineraryDetails)
    source_message_id = uuid4()
    user_id = uuid4()
    repository.get_details_by_source_message.return_value = details

    result = asyncio.run(
        service.find_generated_draft(
            source_message_id=source_message_id,
            user_id=user_id,
        )
    )

    assert result is details
    repository.get_details_by_source_message.assert_awaited_once_with(
        source_message_id=source_message_id,
        user_id=user_id,
    )


def test_get_saved_itinerary_returns_current_version() -> None:
    """The service should return the trip's one current saved plan."""

    service, _, repository = create_service()
    details = MagicMock(spec=ItineraryDetails)
    repository.get_saved_details_for_trip_user.return_value = details

    assert (
        asyncio.run(service.get_saved_itinerary(trip_id=uuid4(), user_id=uuid4()))
        is details
    )


def test_get_saved_itinerary_raises_when_no_saved_version_exists() -> None:
    """An owned trip without a saved version should return not found."""

    service, _, repository = create_service()
    repository.get_saved_details_for_trip_user.return_value = None

    with pytest.raises(ItineraryNotFoundError):
        asyncio.run(service.get_saved_itinerary(trip_id=uuid4(), user_id=uuid4()))


def test_save_draft_supersedes_previous_version_and_commits() -> None:
    """Saving should atomically replace the trip's previous current plan."""

    service, session, repository = create_service()
    itinerary = create_itinerary(status=ItineraryStatus.DRAFT)
    details = ItineraryDetails(itinerary=itinerary, items=[])
    repository.get_for_user_with_trip_lock.return_value = itinerary
    repository.has_items.return_value = True
    repository.get_details_for_user.return_value = details

    result = asyncio.run(
        service.save_itinerary(itinerary_id=itinerary.id, user_id=uuid4())
    )

    assert result is details
    repository.replace_saved_with.assert_awaited_once_with(itinerary=itinerary)
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_save_is_idempotent_for_already_saved_version() -> None:
    """Retrying save should succeed without repeating the replacement update."""

    service, session, repository = create_service()
    itinerary = create_itinerary(status=ItineraryStatus.SAVED)
    details = ItineraryDetails(itinerary=itinerary, items=[])
    repository.get_for_user_with_trip_lock.return_value = itinerary
    repository.has_items.return_value = True
    repository.get_details_for_user.return_value = details

    assert (
        asyncio.run(service.save_itinerary(itinerary_id=itinerary.id, user_id=uuid4()))
        is details
    )

    repository.replace_saved_with.assert_not_awaited()
    session.commit.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("itinerary", "has_items", "error_type"),
    [
        (None, True, ItineraryNotFoundError),
        (
            create_itinerary(status=ItineraryStatus.SUPERSEDED),
            True,
            InvalidItineraryStatusTransitionError,
        ),
        (
            create_itinerary(status=ItineraryStatus.DRAFT),
            False,
            InvalidItineraryDetailsError,
        ),
    ],
)
def test_save_rejects_invalid_target_and_rolls_back(
    itinerary: Itinerary | None,
    has_items: bool,
    error_type: type[Exception],
) -> None:
    """Missing, superseded, and empty itinerary versions cannot be saved."""

    service, session, repository = create_service()
    repository.get_for_user_with_trip_lock.return_value = itinerary
    repository.has_items.return_value = has_items

    with pytest.raises(error_type):
        asyncio.run(service.save_itinerary(itinerary_id=uuid4(), user_id=uuid4()))

    repository.replace_saved_with.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
