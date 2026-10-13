import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.destinations import DestinationRepository
from app.domain.preferences import TravelInterest, TravelStyle


def test_city_candidates_use_one_bounded_country_filtered_query() -> None:
    city_id = uuid4()
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=city_id,
                slug="london-united-kingdom",
                name="London",
                country_code="GB",
                editorial_rank=1,
                interests=["shopping", "history"],
                styles=["culture"],
            )
        ]
    )

    candidates = asyncio.run(
        DestinationRepository(session).list_city_candidates(
            country_code=" gb ",
            interests=(TravelInterest.HISTORY,),
            travel_styles=(TravelStyle.CULTURE,),
        )
    )

    assert len(candidates) == 1
    assert candidates[0].id == city_id
    assert candidates[0].interests == (
        TravelInterest.HISTORY,
        TravelInterest.SHOPPING,
    )
    session.execute.assert_awaited_once()

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()

    assert "is_published is true" in sql
    assert "destination_type =" in sql
    assert "country_code =" in sql
    assert "limit" in sql
    assert "interest_matches desc" in sql
    assert "style_matches desc" in sql
    assert "media_assets" not in sql
    assert "GB" in compiled.params.values()
    assert "city" in compiled.params.values()


def test_no_city_candidates_returns_empty_list() -> None:
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=[])

    result = asyncio.run(
        DestinationRepository(session).list_city_candidates(
            country_code="JP",
            interests=(),
        )
    )

    assert result == []


@pytest.mark.parametrize("limit", [0, 4])
def test_invalid_city_limit_does_not_query_database(limit: int) -> None:
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()

    with pytest.raises(ValueError, match="between 1 and 3"):
        asyncio.run(
            DestinationRepository(session).list_city_candidates(
                country_code="GB",
                interests=(),
                limit=limit,
            )
        )

    session.execute.assert_not_awaited()


@pytest.mark.parametrize("query", ["", "x", "x" * 121])
def test_invalid_city_name_does_not_query_database(query):
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()

    with pytest.raises(ValueError, match="between 2 and 120"):
        asyncio.run(
            DestinationRepository(session).find_published_cities(
                query=query,
                country_code="GB",
            )
        )

    session.execute.assert_not_awaited()


def test_invalid_country_code_does_not_query_database():
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock()

    with pytest.raises(ValidationError):
        asyncio.run(
            DestinationRepository(session).get_published_city(
                destination_id=uuid4(),
                country_code="GBR",
            )
        )

    session.execute.assert_not_awaited()
