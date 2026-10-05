"""Activity media survives persistence and the existing owned itinerary read."""

import asyncio
from datetime import date
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.schemas.itineraries import ItineraryItemResponse
from app.database.models import Trip, User
from app.domain.itineraries import ItineraryItemDraft
from app.domain.media import AssistantMedia
from app.services.itinerary_service import ItineraryService


def test_activity_image_restored_from_database(postgres_url, migrate_database):
    async def scenario():
        engine = create_async_engine(postgres_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(migrate_database)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            user_id, trip_id = uuid4(), uuid4()
            async with factory() as session:
                session.add(
                    User(id=user_id, email="media@example.com", password_hash="test")
                )
                await session.flush()
                session.add(
                    Trip(
                        id=trip_id,
                        user_id=user_id,
                        destination="Hunza",
                        start_date=date(2099, 11, 7),
                        end_date=date(2099, 11, 8),
                        status="draft",
                    )
                )
                await session.commit()
                image = AssistantMedia(
                    url="https://images.example.com/fort.jpg", alt_text="Fort"
                )
                result = await ItineraryService(session=session).create_draft(
                    trip_id=trip_id,
                    user_id=user_id,
                    items=[
                        ItineraryItemDraft(
                            day_number=1,
                            position=1,
                            item_type="place",
                            title="Fort",
                            image=image,
                            starts_at="2099-11-07T09:00:00+05:00",
                            ends_at="2099-11-07T10:00:00+05:00",
                            start_time_zone="Asia/Karachi",
                            end_time_zone="Asia/Karachi",
                        )
                    ],
                )
                itinerary_id = result.itinerary.id
            async with factory() as session:
                restored = await ItineraryService(session=session).get_itinerary(
                    itinerary_id=itinerary_id, user_id=user_id
                )
                response = ItineraryItemResponse.model_validate(restored.items[0])
                assert response.image == image
                assert response.starts_at.isoformat() == "2099-11-07T09:00:00+05:00"
                assert response.start_time_zone == "Asia/Karachi"
                assert response.model_dump(mode="json")["image"]["url"] == str(
                    image.url
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
