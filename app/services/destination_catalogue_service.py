"""Destination View All, detail, place, and gallery use cases."""

import base64
import binascii
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import InvalidCursorError
from app.database.repositories.destinations import DestinationRepository
from app.database.repositories.user_preferences import UserPreferenceRepository
from app.domain.destinations import (
    DestinationCandidate,
    DestinationCollection,
    DestinationDetail,
    DestinationPlaceCandidate,
    DestinationPlaceDetail,
)
from app.domain.errors import DestinationNotFoundError, DestinationPlaceNotFoundError
from app.domain.preferences import RecommendationScope, UserPreferenceSnapshot

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20
DETAIL_PLACE_LIMIT = 10
CURSOR_VERSION = 2
MAX_CURSOR_LENGTH = 512


class CatalogueCursor(BaseModel):
    """Strict transport validation before any cursor value reaches SQL."""

    model_config = ConfigDict(extra="forbid")
    v: StrictInt
    context: str
    key: tuple[
        Annotated[StrictInt, Field(ge=-(2**31), le=2**31 - 1)],
        Annotated[StrictInt, Field(ge=0, le=2**31 - 1)],
        Annotated[str, Field(min_length=1, max_length=120)],
    ]


@dataclass(frozen=True, slots=True)
class DestinationPage:
    """One stable page of destination cards."""

    items: tuple[DestinationCandidate, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DestinationPlacePage:
    """One stable page of places within a destination."""

    items: tuple[DestinationPlaceCandidate, ...]
    next_cursor: str | None


class DestinationCatalogueService:
    """Coordinate bounded database-only destination discovery reads."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        destination_repository: DestinationRepository | None = None,
        preference_repository: UserPreferenceRepository | None = None,
    ) -> None:
        self.destinations = destination_repository or DestinationRepository(session)
        self.preferences = preference_repository or UserPreferenceRepository(session)

    async def list_destinations(
        self,
        *,
        user_id: UUID,
        collection: DestinationCollection,
        scope: RecommendationScope | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> DestinationPage:
        """Return one cursor-paginated ranked destination collection."""

        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

        # Reject malformed input before loading preferences or catalogue rows.
        payload = self._decode_cursor(cursor=cursor) if cursor is not None else None
        preference = None
        resolved_scope = RecommendationScope.BOTH
        if collection is DestinationCollection.SUGGESTED:
            preference = await self.preferences.get_snapshot(user_id=user_id)
            resolved_scope = scope or preference.recommendation_scope
        context = self._collection_context(collection, resolved_scope, preference)
        if payload is not None and payload.context != context:
            raise InvalidCursorError("Pagination cursor is invalid; restart the list")
        if payload is not None:
            rank, editorial_rank, _ = payload.key
            valid_rank = (
                rank < 0 if collection is DestinationCollection.SUGGESTED else rank > 0
            )
            if editorial_rank < 1 or not valid_rank:
                raise InvalidCursorError("Pagination cursor is invalid")
        rows = await self.destinations.list_ranked(
            collection=collection,
            preference=preference,
            scope=resolved_scope,
            limit=limit + 1,
            after=payload.key if payload is not None else None,
        )
        items = rows[:limit]
        return DestinationPage(
            items=tuple(row.destination for row in items),
            next_cursor=self._encode_cursor(context=context, key=items[-1].key)
            if len(rows) > limit
            else None,
        )

    @staticmethod
    def _collection_context(
        collection: DestinationCollection,
        scope: RecommendationScope,
        preference: UserPreferenceSnapshot | None,
    ) -> str:
        # Bind continuation to user and all ranking inputs, not only scope.
        fingerprint = ""
        if preference is not None:
            fingerprint = sha256(
                json.dumps(
                    {
                        "user": str(preference.user_id),
                        "styles": sorted(preference.travel_styles),
                        "interests": sorted(preference.interests),
                        "budget": preference.budget_tier,
                        "pace": preference.trip_pace,
                        "country": preference.home_location.country_code
                        if preference.home_location
                        else None,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        return f"destinations:{collection.value}:{scope.value}:{fingerprint}"

    async def get_destination(self, *, slug: str) -> DestinationDetail:
        """Return a destination, bounded gallery, and place preview."""

        destination = await self.destinations.get_published_by_slug(slug=slug)
        if destination is None:
            raise DestinationNotFoundError("Destination was not found")
        gallery = await self.destinations.list_destination_media(
            destination_id=destination.id
        )
        places = await self.destinations.list_published_places(
            destination_id=destination.id,
            limit=DETAIL_PLACE_LIMIT + 1,
        )
        return DestinationDetail(
            destination=destination,
            gallery=gallery,
            places=tuple(places[:DETAIL_PLACE_LIMIT]),
            places_next_cursor=self._encode_cursor(
                context=f"places:{destination.id}",
                key=(
                    places[DETAIL_PLACE_LIMIT - 1].sort_order,
                    0,
                    str(places[DETAIL_PLACE_LIMIT - 1].id),
                ),
            )
            if len(places) > DETAIL_PLACE_LIMIT
            else None,
        )

    async def list_places(
        self,
        *,
        destination_slug: str,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> DestinationPlacePage:
        """Return one cursor-paginated page of a destination's places."""

        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        destination = await self.destinations.get_published_by_slug(
            slug=destination_slug
        )
        if destination is None:
            raise DestinationNotFoundError("Destination was not found")
        context = f"places:{destination.id}"
        payload = self._decode_cursor(cursor=cursor) if cursor is not None else None
        after = None
        if payload is not None:
            try:
                if (
                    payload.context != context
                    or payload.key[0] < 1
                    or payload.key[1] != 0
                ):
                    raise ValueError
                after = (payload.key[0], UUID(payload.key[2]))
            except ValueError as error:
                raise InvalidCursorError("Pagination cursor is invalid") from error
        rows = await self.destinations.list_published_places(
            destination_id=destination.id,
            limit=limit + 1,
            after=after,
        )
        items = rows[:limit]
        return DestinationPlacePage(
            items=tuple(items),
            next_cursor=self._encode_cursor(
                context=context,
                key=(items[-1].sort_order, 0, str(items[-1].id)),
            )
            if len(rows) > limit
            else None,
        )

    async def get_place(
        self,
        *,
        destination_slug: str,
        place_slug: str,
    ) -> DestinationPlaceDetail:
        """Return one nested place and its bounded gallery."""

        destination = await self.destinations.get_published_by_slug(
            slug=destination_slug
        )
        if destination is None:
            raise DestinationNotFoundError("Destination was not found")
        place = await self.destinations.get_published_place(
            destination_id=destination.id,
            place_slug=place_slug,
        )
        if place is None:
            raise DestinationPlaceNotFoundError("Destination place was not found")
        gallery = await self.destinations.list_place_media(place_id=place.id)
        return DestinationPlaceDetail(
            destination_slug=destination.slug,
            place=place,
            gallery=gallery,
        )

    @staticmethod
    def _encode_cursor(*, context: str, key: tuple[int, int, str]) -> str:
        payload = CatalogueCursor(v=CURSOR_VERSION, context=context, key=key)
        return (
            base64.urlsafe_b64encode(payload.model_dump_json().encode())
            .decode("ascii")
            .rstrip("=")
        )

    @staticmethod
    def _decode_cursor(*, cursor: str) -> CatalogueCursor:
        if not cursor or len(cursor) > MAX_CURSOR_LENGTH:
            raise InvalidCursorError("Pagination cursor is invalid")
        try:
            padding = "=" * (-len(cursor) % 4)
            decoded = base64.b64decode(
                (cursor + padding).encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
            payload = CatalogueCursor.model_validate_json(decoded)
            if payload.v != CURSOR_VERSION:
                raise ValueError
            return payload
        except (binascii.Error, UnicodeError, ValueError) as error:
            raise InvalidCursorError("Pagination cursor is invalid") from error
