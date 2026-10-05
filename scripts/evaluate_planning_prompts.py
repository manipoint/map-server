"""Opt-in live extraction evaluation. Never invoked by the normal test suite."""

import argparse
import asyncio
import json
from time import perf_counter

from app.common.time import utc_now
from app.config import Settings
from app.domain.planning import PlanningState
from app.graph.planning_builder import merge_requirements, structured_call
from app.graph.planning_prompts import REQUIREMENTS_PROMPT, REQUIREMENTS_PROMPT_VERSION
from app.graph.planning_schemas import RequirementExtraction
from app.graph.subgraphs.model_gateway import build_model_gateway, model_call_budget

# Fixed non-personal prompts; expected values test meaning, not exact prose.
CASES = (
    (
        "english_party",
        "Plan a four-day trip to Hunza for two adults, no children.",
        {},
        {"destination": "Hunza", "duration_days": 4, "adults": 2, "minor_count": 0},
        "plan",
    ),
    (
        "roman_urdu_party",
        "Hunza ka 4 din ka trip, hum 2 baray hain aur koi bacha nahi.",
        {},
        {"destination": "Hunza", "duration_days": 4, "adults": 2, "minor_count": 0},
        "plan",
    ),
    (
        "preserve_children",
        "Change only the adults to three.",
        {"adults": 2, "minor_count": 1, "minor_ages": [7]},
        {"adults": 3, "minor_count": 1, "minor_ages": [7]},
        "plan",
    ),
    (
        "clear_interests",
        "Remove all my interests for this trip.",
        {"interests": ["history"]},
        {"interests": []},
        "plan",
    ),
    (
        "new_trip",
        "Start a new trip to Osaka instead.",
        {"destination": "Hunza", "adults": 4},
        {"destination": "Osaka", "adults": None},
        "new_trip",
    ),
    ("weather", "What is the current weather in Lahore?", {}, {}, "search"),
    (
        "currency",
        "Convert 100 USD to EUR at the latest reference rate.",
        {},
        {},
        "search",
    ),
    (
        "ambiguous_party",
        "Plan Japan for four people.",
        {},
        {"destination": "Japan", "adults": None, "minor_count": None},
        "plan",
    ),
)


async def evaluate() -> int:
    gateway = build_model_gateway(Settings())
    failures = 0
    for name, message, initial, expected, intent in CASES:
        state = PlanningState(requirements=initial)
        started = perf_counter()
        passed = False
        error_type = None
        try:
            with model_call_budget(3):
                result = await structured_call(
                    gateway,
                    RequirementExtraction,
                    prompt=REQUIREMENTS_PROMPT,
                    data={
                        "today": utc_now().date().isoformat(),
                        "state": state.model_dump(mode="json"),
                        "profile_preferences": {},
                        "recent_conversation": [],
                        "message": message,
                    },
                )
            base = (
                PlanningState().requirements
                if result.intent == "new_trip"
                else state.requirements
            )
            values = merge_requirements(base, result.selected_updates()).model_dump(
                mode="json"
            )
            passed = result.intent == intent and all(
                values[key] == value for key, value in expected.items()
            )
        except Exception as error:
            error_type = type(error).__name__
        failures += int(not passed)
        print(
            json.dumps(
                {
                    "case": name,
                    "prompt_version": REQUIREMENTS_PROMPT_VERSION,
                    "passed": passed,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "error_type": error_type,
                }
            )
        )
    return int(failures > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly permit paid calls using configured model credentials",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this evaluation makes paid model calls")
    return asyncio.run(evaluate())


if __name__ == "__main__":
    raise SystemExit(main())
