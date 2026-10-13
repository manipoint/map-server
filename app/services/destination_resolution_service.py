"""Country proposals, personalized city options, and fresh city validation."""

from uuid import UUID

from app.database.repositories.destinations import DestinationRepository
from app.domain.city_suggestions import CitySuggestion
from app.domain.destination_selection import (
    CountryRecognition,
    PendingDestinationSelection,
)
from app.domain.planning import PlanningState
from app.domain.planning_preferences import PlanningPreferences
from app.domain.value_objects import CountryCode
from app.services.city_suggestion_service import CitySuggestionService
from app.services.country_recognition_service import CountryRecognitionService


class DestinationResolutionService:
    """Resolve catalogue cities without treating suggestions as consent."""

    def __init__(
        self,
        *,
        repository: DestinationRepository,
        country_recognition: CountryRecognitionService,
    ) -> None:
        self.repository = repository
        self.country_recognition = country_recognition
        self.city_suggestions = CitySuggestionService(repository=repository)

    async def prepare_country(
        self,
        *,
        query: str,
        planning: PlanningState,
        preferences: PlanningPreferences,
        allow_typo: bool = False,
    ) -> tuple[CountryRecognition, PendingDestinationSelection | None]:
        """Recognize an established country input and prepare city options.The graph must distinguish city inputs before enabling typo matching.
        Ambiguous countries require a choice before city suggestions are queried.
        """
        recognition = self.country_recognition.recognize(query, allow_typo=allow_typo)
        # Published city names take precedence over country aliases and typos.
        # Unrecognized/invalid inputs need no catalogue read here.
        if allow_typo and recognition.status in {
            "exact",
            "confirmation_required",
            "ambiguous",
        }:
            cities = await self.repository.find_published_cities(
                query=query, country_code=None
            )
            if cities:
                if recognition.status in {"exact", "ambiguous"}:
                    # A real city may share an exact country name in another
                    # country (Liberia, Costa Rica). Do not silently pick either.
                    proposals = {
                        match.country_code: match for match in recognition.matches
                    }
                    for city in cities:
                        city_country = self.country_recognition.recognize(
                            city.country_code
                        )
                        if city_country.status == "exact":
                            match = city_country.matches[0]
                            proposals[match.country_code] = type(match).model_validate(
                                {**match.model_dump(), "query": query.strip()}
                            )
                    if len(proposals) > 1:
                        return CountryRecognition(
                            status="ambiguous",
                            matches=tuple(
                                type(match).model_validate(
                                    {
                                        **match.model_dump(),
                                        "requires_confirmation": True,
                                    }
                                )
                                for _, match in sorted(proposals.items())[:3]
                            ),
                        ), None
                return CountryRecognition(status="not_recognized"), None
        if recognition.status not in {"exact", "confirmation_required"}:
            return recognition, None
        country = recognition.matches[0]
        options = await self.city_suggestions.suggest(
            country_code=country.country_code,
            planning=planning,
            preferences=preferences,
        )
        return recognition, PendingDestinationSelection(
            country=country, options=options
        )

    async def confirm_country(
        self,
        *,
        recognition: CountryRecognition,
        selected_code: CountryCode,
        planning: PlanningState,
        preferences: PlanningPreferences,
    ) -> PendingDestinationSelection:
        """Accept only a country explicitly chosen from saved proposals."""
        country = next(
            (
                match
                for match in recognition.matches
                if match.country_code == selected_code
            ),
            None,
        )
        if country is None:
            raise ValueError("Selected country was not offered")
        confirmed = type(country).model_validate(
            {**country.model_dump(), "requires_confirmation": False}
        )
        options = await self.city_suggestions.suggest(
            country_code=confirmed.country_code,
            planning=planning,
            preferences=preferences,
        )
        return PendingDestinationSelection(country=confirmed, options=options)

    async def select_offered_city(
        self, *, pending: PendingDestinationSelection, destination_id: UUID
    ) -> CitySuggestion | None:
        """Revalidate an explicitly selected option against current catalogue."""
        if not any(
            option.destination_id == destination_id for option in pending.options
        ):
            raise ValueError("Selected city was not offered")
        return await self.repository.get_published_city(
            destination_id=destination_id, country_code=pending.country.country_code
        )

    async def resolve_city_name(
        self, *, pending: PendingDestinationSelection, query: str
    ) -> tuple[CitySuggestion, ...]:
        """Resolve a user-entered catalogue city within the proposed country.

        Zero matches: request correction or provider-backed resolution.
        One match: the caller may accept the explicitly entered city.
        Two matches: require disambiguation; never select the first automatically.
        """
        return await self.repository.find_published_cities(
            query=query, country_code=pending.country.country_code
        )
