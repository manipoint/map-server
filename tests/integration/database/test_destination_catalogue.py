"""Opt-in real PostgreSQL checks using a private, disposable local cluster."""

import asyncio
import os
import socket
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database.base import Base
from app.database.models import (
    Destination,
    DestinationMedia,
    DestinationPlace,
    MediaAsset,
)
from app.database.repositories.destinations import DestinationRepository
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.destination_ranking import rank_suggested
from app.domain.destinations import DestinationCollection
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    TripPace,
)
from app.domain.trips import CanonicalLocation
from app.services.destination_catalogue_service import DestinationCatalogueService
from app.services.home_discovery_service import HomeDiscoveryService


@pytest.fixture
def postgres_url(tmp_path):
    """Never use application credentials or an existing database."""
    configured = os.environ.get("TEST_POSTGRES_BIN")
    if not configured:
        pytest.skip("Set TEST_POSTGRES_BIN to run isolated PostgreSQL tests")
    binaries = Path(configured)
    data = tmp_path / "data"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        [
            str(binaries / "initdb"),
            "-D",
            str(data),
            "-U",
            "catalogue_test",
            "-A",
            "trust",
            "--no-locale",
            "-E",
            "UTF8",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [
            str(binaries / "pg_ctl"),
            "-D",
            str(data),
            "-l",
            str(tmp_path / "postgres.log"),
            "-o",
            f"-h 127.0.0.1 -p {port} -F -c unix_socket_directories=''",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    try:
        yield f"postgresql+asyncpg://catalogue_test@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [
                str(binaries / "pg_ctl"),
                "-D",
                str(data),
                "-m",
                "immediate",
                "-w",
                "stop",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )


def migrate(connection):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[3] / "alembic")
    )
    scripts = ScriptDirectory.from_config(config)
    revisions = list(reversed(list(scripts.walk_revisions())))
    with Operations.context(
        MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
    ):
        for revision in revisions:
            if revision.revision == "d4f8a2c7e910":
                connection.execute(
                    text(
                        "INSERT INTO app.users (id,email,password_hash) VALUES "
                        "('90000000-0000-4000-8000-000000000001','migration@example.com','test-hash')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO app.user_preferences "
                        "(user_id,travel_style,budget_tier,trip_pace,recommendation_scope) VALUES "
                        "('90000000-0000-4000-8000-000000000001','nature','mid_range','balanced','both')"
                    )
                )
            revision.module.upgrade()
        # Exercise actual populated catalogue downgrade and upgrade.
        catalogue = scripts.get_revision("d4f8a2c7e910")
        indexes = scripts.get_revision("b7e2f9a41063")
        indexes.module.downgrade()
        catalogue.module.downgrade()
        catalogue.module.upgrade()
        indexes.module.upgrade()
    assert (
        connection.execute(
            text(
                "SELECT travel_style FROM app.user_travel_styles WHERE "
                "user_id='90000000-0000-4000-8000-000000000001'"
            )
        ).scalar_one()
        == "nature"
    )
    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": Base.metadata,
            "include_schemas": True,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    assert compare_metadata(context, Base.metadata) == []


def test_real_catalogue_migrations_ranking_pagination_and_gallery(postgres_url):
    asyncio.run(_exercise_catalogue(postgres_url))


async def _exercise_catalogue(url):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repo = DestinationRepository(session)
            skardu = await repo.get_published_by_slug(slug="skardu-pakistan")
            assert skardu is not None
            assert len(await repo.list_published_places(destination_id=skardu.id)) == 4
            assert len(await repo.list_destination_media(destination_id=skardu.id)) == 1
            # New high-popularity records must survive low editorial priority.
            cover_id = skardu.cover_image.id
            for index in range(130):
                destination = Destination(
                    id=uuid4(),
                    slug=f"test-{index:03}",
                    name=f"Test {index}",
                    destination_type="city",
                    country_name="Pakistan",
                    country_code="PK",
                    summary="A test destination with sufficient descriptive text.",
                    full_description="A test destination with sufficient detailed text.",
                    latitude=35,
                    longitude=75,
                    map_zoom=10,
                    budget_tier="mid_range",
                    editorial_rank=1000 + index,
                    is_published=True,
                    is_popular=True,
                    popular_rank=1,
                    is_featured=False,
                )
                session.add(destination)
                await session.flush()
                session.add(
                    DestinationMedia(
                        destination_id=destination.id,
                        media_asset_id=cover_id,
                        role="cover",
                        sort_order=0,
                    )
                )
                session.add(
                    DestinationPlace(
                        id=uuid4(),
                        destination_id=skardu.id,
                        slug=f"place-{index:03}",
                        name=f"Place {index}",
                        place_type="viewpoint",
                        summary="A test place with sufficient descriptive text.",
                        full_description="A test place with sufficient detailed text.",
                        latitude=35,
                        longitude=75,
                        sort_order=1000 + index,
                        is_published=True,
                    )
                )
            # Extra images are returned in order without duplicating cover.
            for index in range(2):
                asset = MediaAsset(
                    id=uuid4(),
                    url=f"https://example.com/gallery-{index}.jpg",
                    mime_type="image/jpeg",
                    alt_text="A gallery image",
                    is_active=True,
                )
                session.add(asset)
                await session.flush()
                session.add(
                    DestinationMedia(
                        destination_id=skardu.id,
                        media_asset_id=asset.id,
                        role="gallery",
                        sort_order=index + 1,
                    )
                )
            await session.commit()
            service = DestinationCatalogueService(session=session)
            seen = []
            cursor = None
            for _ in range(20):
                page = await service.list_destinations(
                    user_id=uuid4(),
                    collection=DestinationCollection.POPULAR,
                    limit=17,
                    cursor=cursor,
                )
                seen.extend(item.id for item in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break
            expected = await session.scalar(
                select(func.count())
                .select_from(Destination)
                .where(
                    Destination.is_published.is_(True),
                    Destination.popular_rank.is_not(None),
                )
            )
            assert len(seen) == len(set(seen)) == expected
            assert len(seen) > 130
            first = await service.list_destinations(
                user_id=uuid4(),
                collection=DestinationCollection.POPULAR,
                limit=1,
            )
            # Deleting/unpublishing the anchor cannot invalidate its seek key.
            await session.execute(
                text("UPDATE app.destinations SET is_published=false WHERE id=:id"),
                {"id": first.items[-1].id},
            )
            next_page = await service.list_destinations(
                user_id=uuid4(),
                collection=DestinationCollection.POPULAR,
                limit=1,
                cursor=first.next_cursor,
            )
            assert next_page.items and next_page.items[0].id != first.items[0].id

            detail = await service.get_destination(slug=skardu.slug)
            assert len(detail.places) == 10
            assert detail.places_next_cursor
            assert len(detail.gallery) == len({item.id for item in detail.gallery}) == 3
            place_ids = [item.id for item in detail.places]
            cursor = detail.places_next_cursor
            while cursor:
                page = await service.list_places(
                    destination_slug=skardu.slug,
                    limit=23,
                    cursor=cursor,
                )
                place_ids.extend(item.id for item in page.items)
                cursor = page.next_cursor
            assert len(place_ids) == len(set(place_ids)) == 134

            snapshot = replace(
                UserPreferenceRepository.empty_snapshot(user_id=uuid4()),
                travel_styles=(TravelStyle.NATURE, TravelStyle.ADVENTURE),
                interests=(TravelInterest.HIKING, TravelInterest.PHOTOGRAPHY),
                budget_tier=BudgetTier.MID_RANGE,
                trip_pace=TripPace.BALANCED,
                home_location=CanonicalLocation(
                    provider="google",
                    provider_location_id="test-home",
                    canonical_name="Lahore, Pakistan",
                    country_code="PK",
                    latitude=31.5,
                    longitude=74.3,
                ),
            )
            preferences = Mock(spec=UserPreferenceRepository)
            preferences.get_snapshot = AsyncMock(return_value=snapshot)
            service = DestinationCatalogueService(
                session=session, preference_repository=preferences
            )
            home = await HomeDiscoveryService(
                session=session,
                preference_repository=preferences,
            ).get_home(user_id=snapshot.user_id)
            for scope, home_items in [
                (RecommendationScope.BOTH, home.suggested),
                (RecommendationScope.LOCAL, home.suggested_local),
                (RecommendationScope.INTERNATIONAL, home.suggested_international),
            ]:
                page = await service.list_destinations(
                    user_id=snapshot.user_id,
                    collection=DestinationCollection.SUGGESTED,
                    scope=scope,
                    limit=6,
                )
                assert page.items == home_items
            # SQL score and Python specification agree on all available candidates.
            rows = []
            after = None
            while True:
                page = await repo.list_ranked(
                    collection=DestinationCollection.SUGGESTED,
                    preference=snapshot,
                    limit=51,
                    after=after,
                )
                rows.extend(item.destination for item in page)
                if len(page) < 51:
                    break
                after = page[-1].key
            assert len(rows) > 100
            assert tuple(rows) == rank_suggested(
                rows, snapshot, scope=RecommendationScope.BOTH
            )
    finally:
        await engine.dispose()
