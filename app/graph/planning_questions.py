"""Deterministic, localized requirement prompts without another model call."""

QUESTIONS = {
    "destination": ("Where would you like to travel?", "Aap kahan jana chahtay hain?"),
    "start_date": (
        "What is your departure date, including year?",
        "Janay ki tareekh aur saal batayein.",
    ),
    "end_date": (
        "What is your return date or trip duration?",
        "Wapsi ki tareekh ya trip kitnay din ka hoga?",
    ),
    "adults": ("How many adults are travelling?", "Kitnay adults safar karein ge?"),
    "minor_count": (
        "How many travellers are under 18 (including infants)?",
        "18 saal se chotay kitnay bachay hain, infants samait?",
    ),
    "minor_ages": ("What is each child's age?", "Har bachay ki umar batayein."),
    "transport": (
        "Will you fly, travel by road or rail, or arrange transport yourself?",
        "Flight, road, rail ya transport aap khud arrange karein ge?",
    ),
    "origin": ("Where are you travelling from?", "Aap kahan se safar karein ge?"),
    "cabin_class": (
        "Which flight cabin class do you prefer?",
        "Flight ki konsi cabin class chahiye?",
    ),
    "infant_seating": (
        "For each infant, will they sit on a lap or have their own seat?",
        "Har infant lap par hoga ya apni seat par?",
    ),
    "needs_lodging": ("Do you need accommodation?", "Kia rehne ke liye hotel chahiye?"),
    "rooms": ("How many rooms do you need?", "Kitnay rooms chahiye?"),
    "budget_decision": (
        "What is your total budget, or is it undecided/no limit?",
        "Total budget kitna hai, ya abhi undecided/no limit hai?",
    ),
    "total_budget": (
        "What is your total trip budget?",
        "Pooray trip ka budget kitna hai?",
    ),
    "budget_currency": (
        "Which currency is your budget in?",
        "Budget kis currency mein hai?",
    ),
}


def clarification_text(fields: tuple[str, ...], language: str) -> str:
    """Ask at most three questions while retaining all missing fields in state."""
    index = 1 if language == "ur-Latn" else 0
    return "\n".join(QUESTIONS[field][index] for field in fields[:3])
