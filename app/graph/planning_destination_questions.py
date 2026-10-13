"""Deterministic questions for pending country/city selection."""

from app.domain.destination_selection import (
    CountryRecognition,
    PendingDestinationSelection,
)


def country_selection_text(recognition: CountryRecognition, language: str) -> str:
    """Render saved country proposals without choosing one for the user."""
    lines = [
        "Kis mulk ka matlab hai?"
        if language == "ur-Latn"
        else "Which country did you mean?"
    ]
    lines.extend(
        f"{number}. {match.country_name} ({match.country_code})"
        for number, match in enumerate(recognition.matches, start=1)
    )
    lines.append(
        "Mulk ka naam, code, ya option number batayein."
        if language == "ur-Latn"
        else "Reply with the country name, code, or option number."
    )
    return "\n".join(lines)


def destination_selection_text(
    pending: PendingDestinationSelection,
    language: str,
) -> str:
    """Render saved options without selecting a destination for the user."""
    country = pending.country.country_name
    roman_urdu = language == "ur-Latn"

    if pending.country.requires_confirmation:
        heading = (
            f"Kya aap ka matlab {country} hai?"
            if roman_urdu
            else f"Did you mean {country}?"
        )
    else:
        heading = (
            f"{country} mein kis shehar ka plan banayein?"
            if roman_urdu
            else f"Which city in {country} would you like to visit?"
        )

    lines = [heading]

    for number, option in enumerate(pending.options, start=1):
        line = f"{number}. {option.name}"

        if option.matched_interests:
            interests = ", ".join(
                interest.value.replace("_", " ")
                for interest in option.matched_interests
            )
            line += (
                f" — aap ke interests se match: {interests}"
                if roman_urdu
                else f" — matches your interests: {interests}"
            )

        lines.append(line)

    if pending.options:
        lines.append(
            "Shehar ka naam ya option number batayein, ya isi mulk ka koi aur shehar chun lein."
            if roman_urdu
            else "Name a city above or its option number, or enter another city in that country."
        )
    else:
        lines.append(
            "Is mulk mein jis shehar jana chahtay hain us ka naam batayein."
            if roman_urdu
            else "Please name the city you would like to visit in that country."
        )

    return "\n".join(lines)
