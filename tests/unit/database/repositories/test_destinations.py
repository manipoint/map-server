"""Tests for bounded destination catalogue hydration."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.destination import Destination, MediaAsset
from app.database.repositories.destinations import DestinationRepository
from app.domain.destinations import DestinationCollection
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle


def create_destination() -> Destination:
    """Create one complete published persistence row."""

    return Destination(
        id=uuid4(),
        slug="hunza-pakistan",
        name="Hunza",
        destination_type="region",
        country_name="Pakistan",
        country_code="PK",
        summary="A mountain destination with trails and expansive valley views.",
        full_description="A complete mountain destination description for testing.",
        latitude=36.3167,
        longitude=74.65,
        map_zoom=9,
        budget_tier=BudgetTier.MID_RANGE.value,
        is_published=True,
        is_featured=True,
        featured_rank=1,
        is_popular=True,
        popular_rank=2,
        editorial_rank=1,
    )


def create_media() -> MediaAsset:
    """Create one active cover image."""

    return MediaAsset(
        id=uuid4(),
        url="https://images.example.com/hunza.jpg",
        mime_type="image/jpeg",
        alt_text="Hunza valley",
        is_active=True,
    )


def create_session(
    rows: list[tuple[Destination, list[str] | None, list[str] | None, MediaAsset, int]],
) -> Mock:
    """Create an async session returning aggregated tag arrays."""

    result = Mock()
    result.all.return_value = rows
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=result)
    return session


def test_catalogue_hydrates_aggregated_tags_in_one_query() -> None:
    """Multiple normalized tags should produce one deterministic candidate."""

    destination = create_destination()
    media = create_media()
    session = create_session(
        [
            (destination, ["nature", "adventure"], ["hiking", "photography"], media, 0),
        ]
    )
    repository = DestinationRepository(session)

    result = [
        row.destination
        for row in asyncio.run(
            repository.list_ranked(
                collection=DestinationCollection.POPULAR,
            )
        )
    ]

    assert len(result) == 1
    assert result[0].styles == (TravelStyle.ADVENTURE, TravelStyle.NATURE)
    assert result[0].interests == (
        TravelInterest.HIKING,
        TravelInterest.PHOTOGRAPHY,
    )
    assert result[0].budget_tier is BudgetTier.MID_RANGE
    assert result[0].cover_image.url.endswith("hunza.jpg")
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert "LIMIT" in str(statement.compile()).upper()


@pytest.mark.parametrize("limit", [0, 52])
def test_catalogue_rejects_unbounded_limits(limit: int) -> None:
    """Callers must not accidentally turn Home into an unbounded catalogue read."""

    session = create_session([])
    repository = DestinationRepository(session)

    with pytest.raises(ValueError, match="between 1 and 51"):
        asyncio.run(
            repository.list_ranked(
                collection=DestinationCollection.POPULAR, limit=limit
            )
        )

    session.execute.assert_not_awaited()
