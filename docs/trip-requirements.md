# Conversational trip requirements

Implemented: `app/domain/trip_requirements.py` and
`app/services/trip_requirements_policy.py` describe partial planning facts and
deterministic missing-field decisions. Persistence, validated extraction/merging, trip creation/linking and graph phase
routing now run in the default planner. See [graph implementation](langgraph.md).

Unknown values remain `None`; explicit zero minors and an undecided budget differ
from missing answers. Requirements reject non-integer ages and non-boolean infant
seating. Existing provider/API age coercion is retained for compatibility. Shared
age types are re-exported from their previous modules to preserve import paths.

Pure rules live in `app/domain/trip_rules.py`: trip date order, flight date order,
distinct locations, lap-infant accompaniment, room allocation, traveler limit,
inclusive duration and interest normalization. Domain, REST, graph and provider
boundaries reuse them, retaining their error translation. Database constraints
remain independent integrity protections. Hotel night limits, same-day flight
returns and same-day rich-content ranges retain their separate contracts.

Trip dates currently require at least two calendar days. `resolved_end_date`
returns the explicit end date or derives it from start date and inclusive duration.
It does not mutate supplied facts or add a derived field to serialized JSON;
callers building final requests must use this property. Revalidating stored
requirements reconstructs it. Contradictions and calendar overflow are rejected.
Completeness uses the resolved date and an explicitly supplied `today`.

Corrections merge raw values and call `model_validate`; unchecked
`model_copy(update=...)` bypasses validation. Contradictory date/duration corrections
need an explicit resolution policy rather than silent data loss. Completeness is
not provider availability or permission to book. Optional interests do not block
planning; infant seating and lodging rooms are conditional requirements.
