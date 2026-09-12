"""Public backend-owned onboarding option catalogue."""

from fastapi import APIRouter, Response

from app.api.schemas.onboarding import (
    OnboardingOptionResponse,
    OnboardingOptionsResponse,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _option(
    option_id: str,
    label: str,
    description: str,
    sort_order: int,
) -> OnboardingOptionResponse:
    return OnboardingOptionResponse(
        id=option_id,
        label=label,
        description=description,
        icon_key=option_id,
        sort_order=sort_order,
    )


@router.get(
    "/options",
    response_model=OnboardingOptionsResponse,
    summary="Get active travel-preference options",
)
async def get_onboarding_options(response: Response) -> OnboardingOptionsResponse:
    """Return cacheable IDs and display metadata without provider calls."""

    response.headers["Cache-Control"] = "public, max-age=3600"
    return OnboardingOptionsResponse(
        version=1,
        travel_styles=[
            _option("adventure", "Adventure", "Outdoor activities and exploration", 1),
            _option("beaches", "Beaches", "Coastal escapes and island time", 2),
            _option("culture", "Culture", "Heritage, arts, and local traditions", 3),
            _option("food", "Food", "Local cuisine and culinary experiences", 4),
            _option("luxury", "Luxury", "Premium stays and refined experiences", 5),
            _option("nature", "Nature", "Landscapes, wildlife, and open spaces", 6),
        ],
        interests=[
            _option("events", "Events", "Festivals and scheduled experiences", 1),
            _option("hiking", "Hiking", "Trails, walks, and mountain routes", 2),
            _option("history", "History", "Historic sites and stories", 3),
            _option("local_culture", "Local Culture", "Communities and traditions", 4),
            _option(
                "nightlife", "Nightlife", "Evening entertainment and social scenes", 5
            ),
            _option("photography", "Photography", "Scenic and visual experiences", 6),
            _option("shopping", "Shopping", "Markets, crafts, and retail", 7),
            _option("wellness", "Wellness", "Restorative and mindful experiences", 8),
            _option("wildlife", "Wildlife", "Animals and natural habitats", 9),
        ],
        budget_tiers=[
            _option("budget", "Budget Friendly", "Lower-cost travel choices", 1),
            _option("mid_range", "Mid Range", "Balanced comfort and cost", 2),
            _option("premium", "Premium", "Higher-comfort travel", 3),
            _option("luxury", "Luxury", "Top-tier stays and experiences", 4),
        ],
        trip_paces=[
            _option("relaxed", "Relaxed", "More free time and fewer daily stops", 1),
            _option("balanced", "Balanced", "A steady mix of plans and free time", 2),
            _option("packed", "Packed", "More activities in each day", 3),
        ],
        recommendation_scopes=[
            _option("local", "Local", "Destinations in your home country", 1),
            _option("international", "International", "Destinations abroad", 2),
            _option("both", "Both", "Local and international destinations", 3),
        ],
    )
