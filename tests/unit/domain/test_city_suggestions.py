from uuid import uuid4

import pytest

from app.domain.city_suggestions import (
    CityCandidate,
    rank_city_candidates,
)
from app.domain.preferences import TravelInterest, TravelStyle


def city(
    name: str,
    *,
    country: str = "GB",
    interests: tuple[TravelInterest, ...] = (),
    styles: tuple[TravelStyle, ...] = (),
    editorial_rank: int = 1,
) -> CityCandidate:
    return CityCandidate(
        id=uuid4(),
        slug=name.lower().replace(" ", "-"),
        name=name,
        country_code=country,
        interests=interests,
        styles=styles,
        editorial_rank=editorial_rank,
    )


def test_interest_match_outranks_style_match() -> None:
    candidates = [
        city("Style City", styles=(TravelStyle.CULTURE,)),
        city(
            "Interest City",
            interests=(TravelInterest.HISTORY,),
            editorial_rank=10,
        ),
    ]

    result = rank_city_candidates(
        candidates,
        country_code="GB",
        interests=(TravelInterest.HISTORY,),
        travel_styles=(TravelStyle.CULTURE,),
    )

    assert result[0].name == "Interest City"
    assert result[0].matched_interests == (TravelInterest.HISTORY,)


def test_candidates_from_another_country_are_excluded() -> None:
    result = rank_city_candidates(
        [
            city("London"),
            city(
                "Tokyo",
                country="JP",
                interests=(TravelInterest.HISTORY,),
            ),
        ],
        country_code="GB",
        interests=(TravelInterest.HISTORY,),
    )

    assert [suggestion.name for suggestion in result] == ["London"]


def test_empty_interests_use_editorial_order_without_claiming_matches() -> None:
    result = rank_city_candidates(
        [
            city("Second", editorial_rank=2),
            city("First", editorial_rank=1),
        ],
        country_code="GB",
        interests=(),
    )

    assert [suggestion.name for suggestion in result] == ["First", "Second"]
    assert all(not suggestion.matched_interests for suggestion in result)


def test_ties_use_slug_and_results_are_limited() -> None:
    result = rank_city_candidates(
        [city("Delta"), city("Charlie"), city("Bravo"), city("Alpha")],
        country_code="GB",
        interests=(),
    )

    assert [suggestion.name for suggestion in result] == [
        "Alpha",
        "Bravo",
        "Charlie",
    ]


def test_no_candidates_returns_empty_tuple() -> None:
    assert (
        rank_city_candidates(
            [],
            country_code="GB",
            interests=(),
        )
        == ()
    )


@pytest.mark.parametrize("limit", [0, 4])
def test_invalid_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 3"):
        rank_city_candidates(
            [],
            country_code="GB",
            interests=(),
            limit=limit,
        )
