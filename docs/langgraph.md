# Conversational planning graph

## Runtime architecture

The default lifespan graph now uses the deterministic planning workflow from the
Roamly R&D review. Authentication, ownership, durable state and transactions remain
in services/repositories. Models cannot create trips or commit data. A validated standalone search intent selects one of five narrow server-controlled capabilities.

```text
Persist user message → reserve generation capacity → acquire ordered conversation lease → claim assistant run
  → restore versioned requirements and previous draft
  → extract intent and explicit requirement changes (one tool-free call,
    with at most one schema-repair retry)
  → validate merged requirements and determine missing fields
      → incomplete: deterministic English/Roman Urdu questions → save reply/state
      → chat: return short reply without changing requirements
      → search: typed standalone MCP lookup → grounded response or guidance
      → complete: persist requirements → reuse fresh research or run bounded research
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

Unknown facts remain unknown. Extraction returns an explicit `changed_fields` mask.
Only listed fields are merged; unlisted nulls required by strict provider schemas
cannot erase known values. A listed null clears the field. All merged requirements
are validated again. Date, budget and party corrections clear obsolete dependent
fields; explicit contradictions are never silently discarded. Dietary/accessibility
constraints and trip pace persist across turns. An explicitly empty interests list
is retained instead of restoring profile defaults.
The graph also sends the current trip's first user request and a bounded tail of
recent user/assistant turns to extraction. The first request is persisted as a
context anchor before model extraction, so it survives a failed first attempt. If
that anchor is older than the normal history window, the service loads only that
conversation-scoped message. Starting a new trip resets the anchor. Assistant text
helps resolve which question is being answered but is not treated as a
user-confirmed fact. If a lookup cannot find the anchor, extraction proceeds from
saved requirements without loading conversation messages outside the ownership
scope.
Inferred flight transport is marked in planning state; changing origin or destination
clears that inference and its cabin preference unless the current turn supplies a
new transport choice. Explicit transport choices survive route edits. Adults-only
party replacements clear obsolete child ages and infant seating. Unknown transport
questions avoid listing unverified road or rail options.
Contradictory dates, ages, seating or budget values preserve previous state and ask
for clarification. A year is not inferred by application code. Mixed Roman Urdu
and English extraction is prompted; deterministic tests mock extraction and do
not prove live-model language accuracy.

The model classifies `chat`, `plan`, `revise`, `new_trip` or `search`. Completeness and
research routing are deterministic. `new_trip` clears the previous association;
ordinary follow-ups retain it. Explicitly supplied owned REST trips seed dates and
locations, then collect remaining requirements. Revisions receive the previous
draft and fresh or reusable research. Changing route or dates creates a new draft trip rather
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
Standalone weather, currency, places, hotels and flight searches do not require a
complete itinerary. Disabled capabilities return an explicit unavailable response.
Current weather is not misrepresented as a forecast. Airport choices and location
candidates are preserved; research requiring clarification stops before synthesis.

Research is reusable for five minutes within the owned conversation when search
arguments and enabled providers are unchanged, evidence has not expired, and the
snapshot has no warnings or unresolved guidance. Pace-only revisions avoid paid
research. Budget/date/route/party changes invalidate the relevant request key.

Evidence retains local IDs, provider source IDs, place source URLs, observation
time and offer expiry where available. Named place/hotel/flight items must refer
to current matching evidence. Their displayed name, location and description are
copied from normalized evidence, not model text. Expired references are rejected.
Generic items use server-owned English/Roman Urdu templates and the summary is
server-owned, preventing invented bookings from bypassing evidence requirements
under an `activity` type. They remain unverified suggestions, not a verified budget.

The output must cover every inclusive day in order. Optional scheduled items must
have paired timezone-aware timestamps, match their day and not overlap. Non-flight
times are omitted when no trusted destination timezone is available. Both flight
legs retain provider timestamps and IANA zones; overnight arrivals are allowed.
Flight times cannot be supplied or changed by the model. Output is
bounded to 100,000 characters, 200 items and 30 trip days. Invalid synthesis gets
one repair attempt without re-running research; a second failure terminates.
Extraction and synthesis use provider-native JSON Schema output with local
Pydantic validation. Each stage gets one bounded repair attempt for schema
validation failures. If extraction remains invalid, it raises a generation failure
instead of persisting a misleading assistant message as a completed reply; the
same durable user request can then use the existing bounded run retry policy.

## Persistence, concurrency and recovery

A conditional database update acquires a user-scoped conversation lease. Its
transaction commits before model/tool calls, returning the connection to the pool.
Other turns wait up to the configured response timeout with exponential polling
and jitter (250 ms initially, capped at 2 seconds before jitter). Database-assigned
turn numbers prevent overtaking pending turns. Admission-deferred, failed and expired turns do not block
newer turns; an older retry returns `stale_request` once a newer turn has advanced
state. History is bounded through the current user turn and sorts replies beside
their source turn, excluding future requests. Completed retries still return the
cached reply before admission or stale-turn checks.

Before writes, the lease token and database-clock expiry are checked again.
Stale workers cannot overwrite a newer lease holder. Validated requirements and
their source-message ID commit before research while retaining the lease. A retry
of that same message resumes research/synthesis without re-extraction. Trip creation,
itinerary version, rich assistant message and assistant-run completion then commit
atomically; generation failure does not discard the accepted requirements. The per-message assistant-run claim
and existing source-message uniqueness retain idempotent retry behavior. Completed
natural-language requests restore itinerary IDs even when the original request
had no `trip_id`. Failure recovery reads ORM identity without triggering lazy I/O
after rollback.

Conversation lease waiting has its own timeout; the existing 75-second default
response deadline then covers graph execution and persistence. The default lease
is 120 seconds. An outer turn deadline also covers pre-graph database work and
ends five seconds before lease expiry. The capacity reservation similarly bounds
the whole admitted operation before its own expiry. Disconnect/shutdown
cancellation releases the conversation lease
when cleanup can run; process death recovers through expiry. This is retry from
persisted application state, not mid-node checkpoint resume.

## Flutter delivery and images

`TravelResponseService` creates or resolves the owned trip and persists the draft.
`AssistantRichContentMapper` builds the existing `rich_response` v1 contract with
place/hotel carousels where evidence exists, plus itinerary preview and traveler
count. The saved reply is restored on retry/history fetch through existing paths.

The version-1 preview now includes `days[].activities`, reusing `ItineraryItemDraft`
and the shared `ItineraryActivity` fields: `day_number`, `position`, `item_type`,
`title`, `description`, `location_name`, `starts_at`, `ends_at`, and optional `image`.
There are at most 30 days and 200 activities in total. Existing replies without
activities remain readable. Updated clients are required for previews beyond the
previous seven-day client limit. Type icons are derived from `item_type`; this
does not introduce booking or route-optimization actions.

Research first attempts an unambiguous exact published catalogue name/slug match.
It reuses active destination cover and place media in bounded database reads,
without per-stop network requests. Curated places replace the redundant external
place search when available; otherwise existing Google place discovery continues.
Unknown/ambiguous catalogue destinations do not borrow unrelated images. Google
photo retrieval is not implemented. Hotel images still require a hotel provider.

The model output schema requires image and timezone fields to be null. The server
attaches images and verified IANA timezones from selected
evidence. Activity image metadata is persisted in `itinerary_items.image`, while
the complete preview is saved in message structured content and restored on replay
or history fetch. Apply migration `c8e3a9f21064` before running the updated backend.
Existing text-only replies are not automatically backfilled.

Activity times are estimates only when the research timezone is known; unknown
times stay null. `start_time_zone` and `end_time_zone` persist IANA names alongside
PostgreSQL UTC instants. API serialization restores the correct local offsets,
including different departure/arrival zones. Older rows without zones remain valid.

## Models and observability

Extraction and synthesis reuse the configured Google → OpenAI → Groq gateway,
without callable tools. A clarification/chat turn normally costs one logical
model call; generation costs two; each stage permits one repair. Physical calls
are capped at 12 per response, including fallback. Input text is capped by
`MODEL_MAX_INPUT_CHARS` (120,000); each provider uses `MODEL_MAX_OUTPUT_TOKENS`
(8,192). These are hard request bounds, not accurate dollar-cost accounting.
Prompts are versioned in `planning_prompts.py`; safe repair feedback includes
validation fields/types or invariant failures, never raw invalid model output.
Separate economy/quality profiles and circuit breakers remain deferred.

HTTP 400/401/403/404/413/422 model errors terminate instead of falling through to
another provider. Transient failures retain bounded fallback; cancellation
propagates. Structured-output validation is separate from transport fallback.
Existing tracing remains enabled according to configuration. Stage latency and
provider-reported input/output token counts are recorded without response text.

## Migration and validation

Apply Alembic revision `d92af5b43107` (including its predecessors) before starting this backend version:

```bash
uv run alembic upgrade head
uv run pytest
uv run ruff check app tests alembic
```

The latest migration adds durable message turn numbers and an admission-deferred
flag, generation admission and
daily accounting tables, and itinerary timezone columns. It backfills turn order
under a table lock; schedule a maintenance window for large message tables. Drain
older workers before deploying the expanded internal planning state. Downgrade
discards admission counters, turn order and timezone metadata, retaining messages.
No deployed database is upgraded by source edits or unit tests.

New tests cover Roman Urdu fixture turns, merging/corrections, no tools during
collection, trip creation without an incoming ID, revisions, bounded repairs,
evidence and schedule rejection, provider degradation/concurrency, ownership,
lease fencing and atomic orchestration. Optional real PostgreSQL tests use a
private disposable cluster (`TEST_POSTGRES_BIN`), never application credentials.

## Deliberately deferred

LangGraph checkpoints/interrupts, autonomous sub-agents, Laya/Jev, strict FIFO
cross-worker job queues, structured requirement forms in Flutter, per-field
provenance/confidence, separate task model profiles, cross-conversation research cache,
verified budget/route feasibility, measured live-model quality and booking remain separate
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

## Admission and session lifecycle

PostgreSQL coordinates `GENERATION_GLOBAL_CONCURRENCY` (16),
`GENERATION_USER_CONCURRENCY` (2), and `GENERATION_DAILY_REQUEST_LIMIT` (100 per
UTC day). Admission holds only a short transaction, never a model call. Failed
admitted attempts still consume daily quota; rejected and completed cached requests
do not. Expiring capacity leases recover after a crash. Old daily counters are
pruned for the user on subsequent admitted work.

Each socket allows `WEBSOCKET_MAX_PENDING_REQUESTS` (4). Session state is checked
before persistence and generation, at token expiry, and every
`WEBSOCKET_AUTH_CHECK_SECONDS` (15) while connected. Revocation on another
worker closes the socket and cancels pending work within that polling interval.

## Live prompt evaluation

Deterministic tests verify contracts and failures, not language-model accuracy.
The opt-in runner includes fixed English/Roman Urdu, correction and standalone
search cases and emits case IDs, pass/fail and latency without prompt/model text:

```bash
uv run python -m scripts.evaluate_planning_prompts --live
```

This command makes paid calls with configured credentials. It is never run by
pytest or automatically during deployment. The strict change-mask contract follows
[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs):
required nullable fields are distinct from the fields explicitly selected to change.
