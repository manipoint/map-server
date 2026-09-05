"""Tests for bounded destination catalogue hydration."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.destination import Destination
from app.database.repositories.destinations import DestinationRepository
from app.domain.preferences import BudgetTier, TravelInterest, TravelStyle


def create_destination() -> Destination:
    """Create one complete published persistence row."""

    return Destination(
        id=uuid4(),
        slug="hunza-pakistan",
        name="Hunza",
        country_name="Pakistan",
        country_code="PK",
        summary="A mountain destination with trails and expansive valley views.",
        image_url="https://images.example.com/hunza.jpg",
        image_alt="Hunza valley",
        latitude=36.3167,
        longitude=74.65,
        budget_tier=BudgetTier.MID_RANGE.value,
        is_published=True,
        is_featured=True,
        featured_rank=1,
        is_popular=True,
        popular_rank=2,
        editorial_rank=1,
    )


def create_session(rows: list[tuple[Destination, str | None, str | None]]) -> Mock:
    """Create an async session returning flattened tag rows."""

    result = Mock()
    result.all.return_value = rows
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=result)
    return session


def test_catalogue_deduplicates_join_products_in_one_query() -> None:
    """Multiple normalized tags should produce one deterministic candidate."""

    destination = create_destination()
    session = create_session(
        [
            (destination, "nature", "hiking"),
            (destination, "nature", "photography"),
            (destination, "adventure", "hiking"),
            (destination, "adventure", "photography"),
        ]
    )
    repository = DestinationRepository(session)

    result = asyncio.run(repository.list_published_catalog())

    assert len(result) == 1
    assert result[0].styles == (TravelStyle.ADVENTURE, TravelStyle.NATURE)
    assert result[0].interests == (
        TravelInterest.HIKING,
        TravelInterest.PHOTOGRAPHY,
    )
    assert result[0].budget_tier is BudgetTier.MID_RANGE
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert "LIMIT" in str(statement.compile()).upper()


@pytest.mark.parametrize("limit", [0, 101])
def test_catalogue_rejects_unbounded_limits(limit: int) -> None:
    """Callers must not accidentally turn Home into an unbounded catalogue read."""

    session = create_session([])
    repository = DestinationRepository(session)

    with pytest.raises(ValueError, match="between 1 and 100"):
        asyncio.run(repository.list_published_catalog(limit=limit))

    session.execute.assert_not_awaited()
