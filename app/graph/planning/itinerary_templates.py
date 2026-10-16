"""Trusted localized text used when normalizing generated itineraries."""

from typing import Literal, TypeAlias

ItineraryLanguage: TypeAlias = Literal["en", "ur-Latn"]
GenericActivityCode: TypeAlias = Literal[
    "breakfast",
    "lunch",
    "dinner",
    "hike",
    "photography",
    "explore",
    "walk",
    "meal",
    "transfer",
    "rest",
    "free_time",
    "note",
]

_DEFAULT_ACTIVITY_CODE: GenericActivityCode = "free_time"

_ENGLISH_TEMPLATES: dict[GenericActivityCode, tuple[str, str]] = {
    "breakfast": (
        "Breakfast",
        "Suggested breakfast break; choose a suitable venue.",
    ),
    "lunch": (
        "Lunch",
        "Suggested lunch break; choose a suitable venue.",
    ),
    "dinner": (
        "Dinner",
        "Suggested dinner break; choose a suitable venue.",
    ),
    "hike": (
        "Optional hike",
        "Allow time for your interests and pace; check trail access and weather.",
    ),
    "photography": (
        "Photography time",
        "Suggested time for photography; confirm access before visiting.",
    ),
    "explore": (
        "Explore the area",
        "Choose a local activity after checking access and opening hours.",
    ),
    "walk": (
        "Optional walk",
        "Choose an accessible route suitable for your group.",
    ),
    "meal": (
        "Meal break",
        "Choose a venue that meets your dietary requirements.",
    ),
    "transfer": (
        "Transfer",
        "Arrange transport; no reservation has been made.",
    ),
    "rest": (
        "Rest break",
        "Unscheduled time to rest.",
    ),
    "free_time": (
        "Free time",
        "Choose an activity that suits your group.",
    ),
    "note": (
        "Planning reminder",
        "Check availability, access and costs before travel.",
    ),
}

_ROMAN_URDU_TEMPLATES: dict[GenericActivityCode, tuple[str, str]] = {
    "breakfast": (
        "Nashta",
        "Nashtay ka tajweez shuda waqfa; munasib jagah chunain.",
    ),
    "lunch": (
        "Dopehar ka khana",
        "Dopehar ke khanay ka waqfa; munasib jagah chunain.",
    ),
    "dinner": (
        "Raat ka khana",
        "Raat ke khanay ka waqfa; munasib jagah chunain.",
    ),
    "hike": (
        "Ikhtiyari hiking",
        "Apni dilchaspi aur raftaar ke mutabiq waqt rakhein; rasta aur mausam check karein.",
    ),
    "photography": (
        "Tasveerain lenay ka waqt",
        "Tasveerain lenay ke liye tajweez shuda waqt; rasai pehle check karein.",
    ),
    "explore": (
        "Ilaqa dekhein",
        "Rasai aur khulne ke auqat tasdeeq kar ke maqami sargarmi chunain.",
    ),
    "walk": (
        "Ikhtiyari sair",
        "Apne group ki zarooriyat ke mutabiq qabil-e-rasai rasta chunain.",
    ),
    "meal": (
        "Khanay ka waqfa",
        "Apni ghizai zarooriyat ke mutabiq jagah chunain.",
    ),
    "transfer": (
        "Safar ka intizam",
        "Transport ka intizam karein; koi booking nahi hui.",
    ),
    "rest": (
        "Aram ka waqfa",
        "Aram ke liye waqt.",
    ),
    "free_time": (
        "Khali waqt",
        "Apne group ke mutabiq sargarmi chunain.",
    ),
    "note": (
        "Yad dehani",
        "Safar se pehle dastiyabi, rasai aur kharch tasdeeq karein.",
    ),
}


def generic_activity_text(
    code: str,
    *,
    language: str,
) -> tuple[str, str]:
    """Return trusted display text for a model-selected generic activity."""

    templates = _ROMAN_URDU_TEMPLATES if language == "ur-Latn" else _ENGLISH_TEMPLATES
    return templates.get(code, templates[_DEFAULT_ACTIVITY_CODE])
