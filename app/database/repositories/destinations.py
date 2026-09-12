"""Bounded reads from the curated destination catalogue."""

from uuid import UUID

from sqlalchemy import and_, case, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from app.database.models.destination import (
    Destination,
    DestinationInterest,
    DestinationMedia,
    DestinationPlace,
    DestinationPlaceMedia,
    DestinationStyle,
    MediaAsset,
)
from app.domain.destination_ranking import (
    BUDGET_ADJACENT_SCORE,
    BUDGET_EXACT_SCORE,
    BUDGET_NEAR_SCORE,
    INTEREST_MATCH_SCORE,
    STYLE_MATCH_SCORE,
)
from app.domain.destinations import (
    DestinationCandidate,
    DestinationCollection,
    DestinationPlaceCandidate,
    DestinationType,
    MediaAssetValue,
    RankedDestination,
)
from app.domain.preferences import (
    BudgetTier,
    RecommendationScope,
    TravelInterest,
    TravelStyle,
    UserPreferenceSnapshot,
)

MAX_GALLERY_IMAGES = 12
MAX_DESTINATION_PLACES = 100


class DestinationRepository:
    """Load published destination content without external providers."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_ranked(
        self,
        *,
        collection: DestinationCollection,
        preference: UserPreferenceSnapshot | None = None,
        scope: RecommendationScope = RecommendationScope.BOTH,
        limit: int = 21,
        after: tuple[int, int, str] | None = None,
    ) -> list[RankedDestination]:
        """Filter and rank the complete catalogue, then hydrate one seek page."""

        if not 1 <= limit <= 51:
            raise ValueError("limit must be between 1 and 51")
        filters = [Destination.is_published.is_(True), self._has_cover()]
        if collection is DestinationCollection.SUGGESTED:
            if preference is None or not preference.personalization_ready:
                return []
            if scope is not RecommendationScope.BOTH:
                if preference.home_location is None:
                    return []
                country = preference.home_location.country_code
                filters.append(
                    Destination.country_code == country
                    if scope is RecommendationScope.LOCAL
                    else Destination.country_code != country
                )
            score = self._preference_score(preference)
            filters.append(score > 0)
            rank = -score
        elif collection is DestinationCollection.POPULAR:
            rank = Destination.popular_rank
            filters.append(Destination.popular_rank.is_not(None))
        else:
            rank = Destination.featured_rank
            filters.append(Destination.featured_rank.is_not(None))
        ranked = (
            select(
                Destination.id,
                rank.label("rank"),
                Destination.editorial_rank,
                Destination.slug,
            )
            .where(*filters)
            .subquery()
        )
        key = (ranked.c.rank, ranked.c.editorial_rank, ranked.c.slug)
        page = select(ranked)
        if after is not None:
            page = page.where(tuple_(*key) > tuple_(*after))
        page = page.order_by(*key).limit(limit).subquery()
        candidates = await self._hydrate(page, ranked=True)
        return candidates

    @staticmethod
    def _has_cover() -> ColumnElement[bool]:
        return (
            select(DestinationMedia.destination_id)
            .join(MediaAsset, MediaAsset.id == DestinationMedia.media_asset_id)
            .where(
                DestinationMedia.destination_id == Destination.id,
                DestinationMedia.role == "cover",
                MediaAsset.is_active.is_(True),
            )
            .exists()
        )

    @staticmethod
    def _preference_score(preference: UserPreferenceSnapshot) -> ColumnElement[int]:
        styles = (
            select(func.count())
            .select_from(DestinationStyle)
            .where(
                DestinationStyle.destination_id == Destination.id,
                DestinationStyle.style.in_(
                    [item.value for item in preference.travel_styles]
                ),
            )
            .correlate(Destination)
            .scalar_subquery()
        )
        interests = (
            select(func.count())
            .select_from(DestinationInterest)
            .where(
                DestinationInterest.destination_id == Destination.id,
                DestinationInterest.interest.in_(
                    [item.value for item in preference.interests]
                ),
            )
            .correlate(Destination)
            .scalar_subquery()
        )
        budgets = list(BudgetTier)
        budget_scores = {
            0: BUDGET_EXACT_SCORE,
            1: BUDGET_ADJACENT_SCORE,
            2: BUDGET_NEAR_SCORE,
        }
        budget = literal(0)
        if preference.budget_tier is not None:
            selected = budgets.index(preference.budget_tier)
            budget = case(
                {
                    item.value: budget_scores.get(abs(index - selected), 0)
                    for index, item in enumerate(budgets)
                },
                value=Destination.budget_tier,
                else_=0,
            )
        return styles * STYLE_MATCH_SCORE + interests * INTEREST_MATCH_SCORE + budget

    async def get_published_by_slug(
        self,
        *,
        slug: str,
    ) -> DestinationCandidate | None:
        """Return one published destination with tags and active cover media."""

        candidates = await self._load_candidates(limit=1, slug=slug)
        return candidates[0] if candidates else None

    async def list_destination_media(
        self,
        *,
        destination_id: UUID,
        limit: int = MAX_GALLERY_IMAGES,
    ) -> tuple[MediaAssetValue, ...]:
        """Return active cover and gallery media in presentation order."""

        result = await self.session.execute(
            select(MediaAsset)
            .join(
                DestinationMedia,
                DestinationMedia.media_asset_id == MediaAsset.id,
            )
            .where(
                DestinationMedia.destination_id == destination_id,
                MediaAsset.is_active.is_(True),
            )
            .order_by(DestinationMedia.sort_order, MediaAsset.id)
            .limit(limit)
        )
        return tuple(self._to_media(asset) for asset in result.scalars().all())

    async def list_published_places(
        self,
        *,
        destination_id: UUID,
        limit: int = MAX_DESTINATION_PLACES,
        after: tuple[int, UUID] | None = None,
    ) -> list[DestinationPlaceCandidate]:
        """Return bounded published places with optional active cover media."""

        if not 1 <= limit <= MAX_DESTINATION_PLACES:
            raise ValueError(f"limit must be between 1 and {MAX_DESTINATION_PLACES}")
        statement = (
            select(DestinationPlace, MediaAsset)
            .outerjoin(
                DestinationPlaceMedia,
                and_(
                    DestinationPlaceMedia.destination_place_id == DestinationPlace.id,
                    DestinationPlaceMedia.role == "cover",
                ),
            )
            .outerjoin(
                MediaAsset,
                and_(
                    MediaAsset.id == DestinationPlaceMedia.media_asset_id,
                    MediaAsset.is_active.is_(True),
                ),
            )
            .where(
                DestinationPlace.destination_id == destination_id,
                DestinationPlace.is_published.is_(True),
            )
            .order_by(DestinationPlace.sort_order, DestinationPlace.id)
            .limit(limit)
        )
        if after is not None:
            statement = statement.where(
                tuple_(DestinationPlace.sort_order, DestinationPlace.id)
                > tuple_(*after)
            )
        result = await self.session.execute(statement)
        return [
            self._to_place(place=place, cover=cover) for place, cover in result.all()
        ]

    async def get_published_place(
        self,
        *,
        destination_id: UUID,
        place_slug: str,
    ) -> DestinationPlaceCandidate | None:
        """Return one published place belonging to the selected destination."""

        result = await self.session.execute(
            select(DestinationPlace, MediaAsset)
            .outerjoin(
                DestinationPlaceMedia,
                and_(
                    DestinationPlaceMedia.destination_place_id == DestinationPlace.id,
                    DestinationPlaceMedia.role == "cover",
                ),
            )
            .outerjoin(
                MediaAsset,
                and_(
                    MediaAsset.id == DestinationPlaceMedia.media_asset_id,
                    MediaAsset.is_active.is_(True),
                ),
            )
            .where(
                DestinationPlace.destination_id == destination_id,
                DestinationPlace.slug == place_slug,
                DestinationPlace.is_published.is_(True),
            )
        )
        row = result.one_or_none()
        return None if row is None else self._to_place(place=row[0], cover=row[1])

    async def list_place_media(
        self,
        *,
        place_id: UUID,
        limit: int = MAX_GALLERY_IMAGES,
    ) -> tuple[MediaAssetValue, ...]:
        """Return active place media in presentation order."""

        result = await self.session.execute(
            select(MediaAsset)
            .join(
                DestinationPlaceMedia,
                DestinationPlaceMedia.media_asset_id == MediaAsset.id,
            )
            .where(
                DestinationPlaceMedia.destination_place_id == place_id,
                MediaAsset.is_active.is_(True),
            )
            .order_by(DestinationPlaceMedia.sort_order, MediaAsset.id)
            .limit(limit)
        )
        return tuple(self._to_media(asset) for asset in result.scalars().all())

    async def _load_candidates(
        self,
        *,
        limit: int,
        slug: str | None = None,
    ) -> list[DestinationCandidate]:
        destination_ids = (
            select(Destination.id)
            .where(Destination.is_published.is_(True))
            .order_by(Destination.editorial_rank, Destination.id)
            .limit(limit)
        )
        if slug is not None:
            destination_ids = destination_ids.where(Destination.slug == slug)
        return [
            item.destination for item in await self._hydrate(destination_ids.subquery())
        ]

    async def _hydrate(
        self,
        page: Subquery,
        *,
        ranked: bool = False,
    ) -> list[RankedDestination]:
        """Aggregate tags only for page rows; avoid style x interest row products."""

        styles = (
            select(func.array_agg(DestinationStyle.style))
            .where(DestinationStyle.destination_id == Destination.id)
            .correlate(Destination)
            .scalar_subquery()
        )
        interests = (
            select(func.array_agg(DestinationInterest.interest))
            .where(DestinationInterest.destination_id == Destination.id)
            .correlate(Destination)
            .scalar_subquery()
        )
        rank = page.c.rank if ranked else literal(0)
        ordering = (
            (page.c.rank, page.c.editorial_rank, page.c.slug)
            if ranked
            else (Destination.editorial_rank, Destination.id)
        )
        result = await self.session.execute(
            select(Destination, styles, interests, MediaAsset, rank)
            .join(page, page.c.id == Destination.id)
            .join(
                DestinationMedia,
                and_(
                    DestinationMedia.destination_id == Destination.id,
                    DestinationMedia.role == "cover",
                ),
            )
            .join(
                MediaAsset,
                and_(
                    MediaAsset.id == DestinationMedia.media_asset_id,
                    MediaAsset.is_active.is_(True),
                ),
            )
            .order_by(*ordering)
        )
        return [
            RankedDestination(
                destination=self._to_candidate(
                    destination=destination,
                    cover=cover,
                    styles={TravelStyle(value) for value in (styles or [])},
                    interests={TravelInterest(value) for value in (interests or [])},
                ),
                key=(rank, destination.editorial_rank, destination.slug),
            )
            for destination, styles, interests, cover, rank in result.all()
        ]

    @classmethod
    def _to_candidate(
        cls,
        *,
        destination: Destination,
        cover: MediaAsset,
        styles: set[TravelStyle],
        interests: set[TravelInterest],
    ) -> DestinationCandidate:
        return DestinationCandidate(
            id=destination.id,
            slug=destination.slug,
            name=destination.name,
            destination_type=DestinationType(destination.destination_type),
            country_name=destination.country_name,
            country_code=destination.country_code,
            summary=destination.summary,
            full_description=destination.full_description,
            cover_image=cls._to_media(cover),
            latitude=destination.latitude,
            longitude=destination.longitude,
            map_zoom=destination.map_zoom,
            budget_tier=BudgetTier(destination.budget_tier),
            styles=tuple(sorted(styles, key=lambda value: value.value)),
            interests=tuple(sorted(interests, key=lambda value: value.value)),
            editorial_rank=destination.editorial_rank,
            featured_rank=destination.featured_rank,
            popular_rank=destination.popular_rank,
        )

    @classmethod
    def _to_place(
        cls,
        *,
        place: DestinationPlace,
        cover: MediaAsset | None,
    ) -> DestinationPlaceCandidate:
        return DestinationPlaceCandidate(
            id=place.id,
            slug=place.slug,
            name=place.name,
            place_type=place.place_type,
            summary=place.summary,
            full_description=place.full_description,
            latitude=place.latitude,
            longitude=place.longitude,
            address=place.address,
            sort_order=place.sort_order,
            is_featured=place.is_featured,
            cover_image=cls._to_media(cover) if cover is not None else None,
        )

    @staticmethod
    def _to_media(asset: MediaAsset) -> MediaAssetValue:
        return MediaAssetValue(
            id=asset.id,
            url=asset.url,
            alt_text=asset.alt_text,
            caption=asset.caption,
            width=asset.width,
            height=asset.height,
        )
