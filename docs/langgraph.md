# Conversational planning graph

## Runtime architecture

The default lifespan graph now uses the deterministic planning workflow from the
Roamly R&D review. Authentication, ownership, durable state and transactions remain
in services/repositories. Models cannot create trips, choose tools or commit data.

```text
Persist user message → acquire conversation lease → claim assistant run
  → restore versioned requirements and previous draft
  → extract intent and explicit requirement changes (one tool-free model call)
  → validate merged requirements and determine missing fields
      → incomplete: deterministic English/Roman Urdu questions → save reply/state
      → chat: return short reply without changing requirements
      → complete: parallel bounded research
          → compact verified evidence
          → tool-free itinerary synthesis
          → validate (at most one repair)
          → atomically save trip, itinerary, planning state and rich reply
  → deliver existing WebSocket completion contract
```

`app/graph/builder.py` delegates to `planning_builder.py` when a research service
is supplied. Lifespan supplies it and constructs an **unbound** model gateway.
The older bounded ReAct builder remains available for existing direct callers and
tests; it is no longer the default conversational planner.

## State and corrections

`PlanningState` is stored in `app.conversations.planning_state` as schema version 1.
It contains partial `TripRequirements`, phase, language, revision, missing fields,
the last generated draft and its compact research evidence. The conversation's
`planning_trip_id` is a separate foreign key, never a model-supplied identifier.

Unknown facts remain unknown. Extraction returns a partial patch: omission retains
a value; explicit null clears it. The whole merged object is validated again.
Contradictory dates, ages, seating or budget values preserve previous state and ask
for clarification. A year is not inferred by application code. Mixed Roman Urdu
and English extraction is prompted; deterministic tests mock extraction and do
not prove live-model language accuracy.

The model classifies only `chat`, `plan`, `revise` or `new_trip`. Completeness and
research routing are deterministic. `new_trip` clears the previous association;
ordinary follow-ups retain it. Explicitly supplied owned REST trips seed dates and
locations, then collect remaining requirements. Revisions receive the previous
draft and fresh research. Changing route or dates creates a new draft trip rather
than rewriting an existing saved trip.

Collection questions use up to three templates per turn. Their missing-field
codes persist internally; they currently travel to Flutter as ordinary text
completion messages, not a new structured requirements-form event.

## Research and grounding

`PlanningResearchService` maps complete requirements through `TripRequestMapper`.
It runs relevant enabled searches concurrently, with a 15-second bound per branch:

- places: up to five compact, deduplicated results;
- lodging: up to three hotel options when lodging is requested and available;
- flights: up to three round-trip results only when that capability is available.

Travelport currently supports one-way only, so planner round-trip research is
explicitly disabled. A one-way search is never silently substituted. No hotel
provider is currently initialized. Missing providers, empty results and expected
search failures produce visible warnings while allowing an unverified draft.
Current weather is not misrepresented as a forecast for future travel dates.

Evidence retains local IDs, provider source IDs, place source URLs, observation
time and offer expiry where available. Named place/hotel/flight items must refer
to current matching evidence. Their displayed name, location and description are
copied from normalized evidence, not model text. Expired references are rejected.
Generic activities/meals/transfers remain unverified suggestions. This is not
semantic proof of every natural-language sentence or a verified total budget.

The output must cover every inclusive day in order. Optional scheduled items must
have both timezone-aware timestamps, match their day and not overlap. Output is
bounded to 100,000 characters, 200 items and 30 trip days. Invalid synthesis gets
one repair attempt without re-running research; a second failure terminates.
Output uses JSON schema instructions plus Pydantic validation, not provider-native
constrained decoding. Requirement extraction has no automatic repair loop.

## Persistence, concurrency and recovery

A conditional database update acquires a user-scoped conversation lease. Its
transaction commits before model/tool calls, returning the connection to the pool.
Other turns wait up to the configured response timeout. Acquisition order is not
a strict FIFO message queue; clients should wait for each clarification reply.
Independent conversations can run concurrently.

Before writes, the lease token and database-clock expiry are checked again.
Stale workers cannot overwrite a newer lease holder. Requirements, trip creation,
itinerary version, rich assistant message and assistant-run completion commit
in one transaction. Failure rolls them back. The per-message assistant-run claim
and existing source-message uniqueness retain idempotent retry behavior. Completed
natural-language requests restore itinerary IDs even when the original request
had no `trip_id`. Failure recovery reads ORM identity without triggering lazy I/O
after rollback.

Conversation lease waiting has its own timeout; the existing 75-second default
response deadline then covers graph execution and persistence. The default lease
is 120 seconds. Disconnect/shutdown cancellation releases the conversation lease
when cleanup can run; process death recovers through expiry. This is retry from
persisted application state, not mid-node checkpoint resume.

## Flutter delivery and images

`TravelResponseService` creates or resolves the owned trip and persists the draft.
`AssistantRichContentMapper` builds the existing `rich_response` v1 contract with
place/hotel carousels where evidence exists, plus itinerary preview and traveler
count. The saved reply is restored on retry/history fetch through existing paths.

No Flutter contract migration is required for these existing sections. Hotel
photos are copied only from normalized provider data. The current place schema
has no photo field, so place cards have no image. This does **not** implement the
full image-rich mockup, a hotel provider, or live booking.

## Models and observability

Extraction and synthesis reuse the configured Groq → Google → OpenAI gateway,
without callable tools. A clarification/chat turn normally costs one logical
model call; generation costs two; repair adds at most one. Provider fallback may
increase physical calls. Separate economy/quality model settings, measured cost
budgets and circuit breakers are not added here.

HTTP 400/401/403/404/413/422 model errors terminate instead of falling through to
another provider. Transient failures retain bounded fallback; cancellation
propagates. Structured-output validation is separate from transport fallback.
Existing tracing remains enabled according to configuration. Stage latency and
provider-reported input/output token counts are recorded without response text.

## Migration and validation

Apply Alembic revision `ab72c4e91035` before starting this backend version:

```bash
uv run alembic upgrade head
uv run pytest
uv run ruff check app tests alembic
```

The migration adds four conversation columns and a nullable trip foreign key.
Its downgrade discards collected requirements, snapshots and sticky associations.
No deployed database is upgraded by source edits or unit tests.

New tests cover Roman Urdu fixture turns, merging/corrections, no tools during
collection, trip creation without an incoming ID, revisions, bounded repairs,
evidence and schedule rejection, provider degradation/concurrency, ownership,
lease fencing and atomic orchestration. Optional real PostgreSQL tests use a
private disposable cluster (`TEST_POSTGRES_BIN`), never application credentials.

## Deliberately deferred

LangGraph checkpoints/interrupts, autonomous sub-agents, Laya/Jev, strict FIFO
cross-worker job queues, structured requirement forms in Flutter, per-field
provenance/confidence, separate task model profiles, cross-request research cache,
verified budget/route feasibility, live-provider evals and booking remain separate
work. These are not implied by having a compiled graph or passing mocked tests.

The graph uses the documented [StateGraph nodes and conditional edges](https://docs.langchain.com/oss/python/langgraph/graph-api).
[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
is a distinct checkpoint facility; this implementation deliberately keeps durable
business state in application tables and does not claim checkpoint resume.

## Model response normalization and diagnostics

`app/graph/model_response.py` is shared by the gateway, structured JSON reader
and plain-text response node. It accepts strings and visible `text`/`output_text`
blocks, joins chunks without changing their boundaries, and excludes reasoning,
images and unknown blocks. Gateway normalization preserves message metadata and
tool calls without mutating the provider response. Empty, malformed and truncated
responses cannot be accepted as successful answers. Explicit refusals terminate
without provider fallback or synthesis repair.

Failure logs include allowlisted finish reasons, content kind/count, HTTP status
and provider error codes (including wrapped Google SDK errors). They never include
raw model content, reasoning, prompts or provider error bodies. A 429 alone does
not establish context overflow: `insufficient_quota`, `rate_limit_exceeded` and
`context_length_exceeded` are distinct diagnostics when exposed by the SDK.
Actual quota/credential remediation remains an account configuration task.

Known persistence limitation: requirements from a turn are staged only after the
whole graph completes. If extraction succeeds but research/synthesis fails or is
cancelled, that turn's extracted updates are not saved, although its user message
is already durable. Earlier committed requirements survive. Separately committing
validated requirements before generation needs a dedicated transaction/idempotency
change; response normalization does not resolve that limitation.
