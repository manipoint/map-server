"""City resolution checks against an isolated PostgreSQL database."""

import asyncio
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database.models import Destination
from app.database.repositories.destinations import DestinationRepository


def test_city_resolution_filters_and_revalidates(
    postgres_url,
    migrate_database,
):
    asyncio.run(exercise_city_resolution(postgres_url, migrate_database))


async def exercise_city_resolution(url, migrate):
    engine = create_async_engine(url)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate)

        async with AsyncSession(
            engine,
            expire_on_commit=False,
        ) as session:
            repository = DestinationRepository(session)

            def destination(
                slug,
                *,
                name="Review City",
                country_code="AD",
                kind="city",
                published=True,
                rank=1,
            ):
                return Destination(
                    id=uuid4(),
                    slug=slug,
                    name=name,
                    destination_type=kind,
                    country_name=("Andorra" if country_code == "AD" else "Japan"),
                    country_code=country_code,
                    summary=("A synthetic destination used for isolated tests."),
                    full_description=(
                        "Synthetic catalogue content for validating city "
                        "resolution against an isolated database."
                    ),
                    latitude=0,
                    longitude=0,
                    map_zoom=10,
                    budget_tier="mid_range",
                    editorial_rank=rank,
                    is_published=published,
                    is_featured=False,
                    is_popular=False,
                )

            first = destination("resolution-first", rank=1)
            second = destination("resolution-second", rank=2)
            third = destination("resolution-third", rank=3)
            hidden = destination(
                "resolution-hidden",
                published=False,
            )
            region = destination(
                "resolution-region",
                kind="region",
            )
            wrong_country = destination(
                "resolution-other-country",
                country_code="JP",
            )
            unique = destination(
                "resolution-unique",
                name="Unique City",
                rank=4,
            )

            session.add_all(
                [
                    first,
                    second,
                    third,
                    hidden,
                    region,
                    wrong_country,
                    unique,
                ]
            )
            await session.flush()

            # A published city requires no cover image to be eligible.
            selected = await repository.get_published_city(
                destination_id=first.id,
                country_code="AD",
            )
            assert selected is not None
            assert selected.destination_id == first.id
            assert selected.name == first.name

            # Revalidation enforces publication, city type, and country.
            for rejected in (hidden, region, wrong_country):
                assert (
                    await repository.get_published_city(
                        destination_id=rejected.id,
                        country_code="AD",
                    )
                    is None
                )

            assert (
                await repository.get_published_city(
                    destination_id=first.id,
                    country_code="JP",
                )
                is None
            )
            assert (
                await repository.get_published_city(
                    destination_id=uuid4(),
                    country_code="AD",
                )
                is None
            )

            # Duplicate names remain ambiguous, bounded to two results.
            # Global lookup is used only to avoid treating a known city as a
            # country typo; selection still uses the scoped query below.
            global_matches = await repository.find_published_cities(
                query="Review City", country_code=None
            )
            assert [item.destination_id for item in global_matches] == [
                first.id,
                wrong_country.id,
            ]
            matches = await repository.find_published_cities(
                query="  REVIEW CITY  ",
                country_code=" ad ",
            )
            assert [item.destination_id for item in matches] == [
                first.id,
                second.id,
            ]

            # An exact slug resolves one identity.
            matches = await repository.find_published_cities(
                query=second.slug,
                country_code="AD",
            )
            assert [item.destination_id for item in matches] == [second.id]

            matches = await repository.find_published_cities(
                query="Unique City",
                country_code="AD",
            )
            assert [item.destination_id for item in matches] == [unique.id]

            assert (
                await repository.find_published_cities(
                    query="Missing City",
                    country_code="AD",
                )
                == ()
            )

            # A previously eligible option must fail after unpublication.
            first.is_published = False
            await session.flush()

            assert (
                await repository.get_published_city(
                    destination_id=first.id,
                    country_code="AD",
                )
                is None
            )
            matches = await repository.find_published_cities(
                query="Review City",
                country_code="AD",
            )
            assert [item.destination_id for item in matches] == [
                second.id,
                third.id,
            ]

    finally:
        await engine.dispose()
