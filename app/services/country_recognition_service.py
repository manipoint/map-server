"""Offline country recognition with conservative spelling proposals."""

import unicodedata
from dataclasses import dataclass
from types import MappingProxyType

import pycountry
from rapidfuzz.distance import DamerauLevenshtein

from app.domain.destination_selection import CountryMatch, CountryRecognition

MAX_QUERY_LENGTH = 120
MAX_COUNTRY_MATCHES = 3


def _normalize(value: str) -> str:
    """Normalize case, accents, punctuation, and repeated whitespace."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    characters = (
        character for character in decomposed if not unicodedata.combining(character)
    )
    cleaned = "".join(
        character if character.isalnum() else " " for character in characters
    )
    return " ".join(cleaned.split())


@dataclass(frozen=True, slots=True)
class _Country:
    code: str
    name: str


class CountryRecognitionService:
    """Build the bounded country index once and reuse it across requests."""

    def __init__(self) -> None:
        countries: dict[str, _Country] = {}
        names: dict[str, set[str]] = {}

        def add_name(name: str, code: str) -> None:
            normalized = _normalize(name)
            if normalized:
                names.setdefault(normalized, set()).add(code)

        for record in pycountry.countries:
            code = str(record.alpha_2)
            countries[code] = _Country(code=code, name=str(record.name))

            for field in ("name", "official_name", "common_name"):
                name = getattr(record, field, None)
                if isinstance(name, str):
                    add_name(name, code)

        aliases = {
            "UK": "GB",
            "U.K.": "GB",
            "Great Britain": "GB",
            "USA": "US",
            "U.S.A.": "US",
            "United States of America": "US",
            "UAE": "AE",
            "Turkey": "TR",
            "Türkiye": "TR",
            "South Korea": "KR",
            "North Korea": "KP",
            "Russia": "RU",
            "Vietnam": "VN",
            "Laos": "LA",
            "Bolivia": "BO",
            "Venezuela": "VE",
            "Iran": "IR",
            "Tanzania": "TZ",
            "Syria": "SY",
            "Moldova": "MD",
        }
        for alias, code in aliases.items():
            if code not in countries:
                raise RuntimeError(f"Country dataset is missing code {code}")
            add_name(alias, code)

        names[_normalize("Congo")] = {"CG", "CD"}
        names[_normalize("Korea")] = {"KR", "KP"}
        exact = {name: frozenset(codes) for name, codes in names.items()}
        for record in pycountry.countries:
            for field in ("alpha_2", "alpha_3"):
                exact[_normalize(str(getattr(record, field)))] = frozenset(
                    {str(record.alpha_2)}
                )

        self._countries = MappingProxyType(countries)
        self._exact = MappingProxyType(exact)
        self._names = tuple(
            (name, tuple(sorted(codes)))
            for name, codes in sorted(names.items())
            if len(name) >= 4
        )

    def recognize(
        self,
        query: str | None,
        *,
        allow_typo: bool = False,
    ) -> CountryRecognition:
        """Recognize a country input, not a complete travel message.

        Enable typo matching only after the caller establishes country intent
        or rules out a known city. Similarity alone cannot distinguish a city
        from a misspelled country.
        """
        if query is None:
            return CountryRecognition(status="invalid_input")
        original = query.strip()
        if not 2 <= len(original) <= MAX_QUERY_LENGTH:
            return CountryRecognition(status="invalid_input")
        normalized = _normalize(original)
        if not normalized:
            return CountryRecognition(status="invalid_input")
        exact_code = self._exact.get(normalized)
        if exact_code:
            return self._result(original, sorted(exact_code), corrected=False)

        if not allow_typo or len(normalized) < 4:
            return CountryRecognition(status="not_recognized")
        # Conservative defaults: one edit for shorter names, two for longer.
        # All fuzzy results require confirmation regardless of score.

        max_distance = 1 if len(normalized) < 8 else 2
        best_distances: dict[str, int] = {}

        for name, codes in self._names:
            if abs(len(name) - len(normalized)) > max_distance:
                continue
            distance = DamerauLevenshtein.distance(
                normalized, name, score_cutoff=max_distance
            )
            if distance > max_distance:
                continue
            similarity = 1 - distance / max(len(normalized), len(name))
            if similarity < 0.80:
                continue

            for code in codes:
                previous = best_distances.get(code)
                if previous is None or distance < previous:
                    best_distances[code] = distance
        if not best_distances:
            return CountryRecognition(status="not_recognized")
        ordered_codes = sorted(
            best_distances, key=lambda code: (best_distances[code], code)
        )[:MAX_COUNTRY_MATCHES]

        return self._result(original, ordered_codes, corrected=True)

    def _result(
        self, query: str, codes: list[str], *, corrected: bool
    ) -> CountryRecognition:
        ambiguous = len(codes) > 1
        confirmation = corrected or ambiguous
        status = (
            "ambiguous"
            if ambiguous
            else "confirmation_required"
            if corrected
            else "exact"
        )
        return CountryRecognition(
            status=status,
            matches=tuple(
                CountryMatch(
                    country_code=code,
                    country_name=self._countries[code].name,
                    query=query,
                    requires_confirmation=confirmation,
                )
                for code in codes
            ),
        )
