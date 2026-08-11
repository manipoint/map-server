# LangGraph Design

## Goals

The graph must minimize unnecessary LLM calls, support missing-input pauses, isolate tool errors from model errors, resume safely after failure, and produce stable structured output for Flutter.

Authentication and WebSocket transport remain outside the graph. A request enters only after FastAPI has authenticated the session and validated the outer event envelope.

## State contract

The initial state should be a typed mapping with compact, serializable values:

```python
class TravelGraphState(TypedDict):
    request_id: str
    user_id: str
    conversation_id: str
    request_type: str
    raw_input: str | None
    structured_input: dict
    intent: str | None
    missing_fields: list[str]
    validation_errors: list[str]
    model_profile: str
    provider_index: int
    retry_count: int
    tool_requests: list[dict]
    tool_result_ids: list[str]
    compact_results: dict
    search_id: str | None
    itinerary_id: str | None
    final_response: dict | None
    error_type: str | None
    error_message: str | None
```

Large provider payloads, secrets, HTTP clients, database sessions, and WebSocket objects must never be checkpointed. Persist large results first and keep identifiers plus compact top results in state.

## Main graph

```mermaid
flowchart TD
    start(["Start"]) --> load["Load context"]
    load --> validate["Validate request"]
    validate --> valid{"Request valid?"}
    valid -->|"No"| error["Build error"]
    error --> finish(["Finish"])
    valid -->|"Yes"| route{"Request route"}
    route -->|"Structured"| direct["Direct tool flow"]
    route -->|"Natural language"| extract["Extract intent"]
    route -->|"Itinerary"| itinerary["Itinerary flow"]
    route -->|"Unsupported"| unsupported["Unsupported response"]
    extract --> slots["Validate slots"]
    slots --> complete{"Fields complete?"}
    complete -->|"No"| interrupt["Request user input"]
    interrupt -.->|"Resume"| slots
    complete -->|"Yes"| intent{"Intent route"}
    intent --> direct
    intent --> itinerary
    direct --> persist["Persist result"]
    itinerary --> persist
    persist --> response["Build response"]
    response --> finish
    unsupported --> finish
```

## Node responsibilities

### Input nodes

- `load_context`: loads a compact conversation summary, user preferences, and referenced trip metadata.
- `validate_request`: checks supported event version, input length, IDs, and request ownership assumptions.
- `extract_intent`: uses deterministic rules first and an economy model only when natural language remains ambiguous.
- `validate_slots`: validates travel dates, locations, travelers, rooms, currency, and configured result limits.

### Tool nodes

- `search_flights`
- `search_hotels`
- `search_places`
- `get_weather`
- `convert_currency`

Every tool node receives a validated domain request. It never receives arbitrary prompt text or user-selected URLs.

### Processing nodes

- `normalize_results`: enforces provider-independent response schemas.
- `rank_results`: applies deterministic ranking using explicit price, duration, distance, rating, and user filters.
- `aggregate_results`: joins independent itinerary branches.
- `compact_for_llm`: removes unused fields and limits each category before synthesis.

### Persistence nodes

- `persist_search`: stores the request and bounded offer snapshots idempotently.
- `persist_messages`: appends user-visible messages, not internal reasoning.
- `persist_itinerary`: stores a validated itinerary and items transactionally.
- `record_usage`: records provider/model, token, latency, fallback, and estimated cost metadata.

### Response nodes

- `request_user_input`: interrupts execution with stable missing-field codes.
- `build_structured_response`: creates Flutter card/list payloads without an LLM.
- `build_final_response`: validates synthesized itinerary output before returning it.
- `build_error`: maps typed internal errors to a public code and safe message.

## Direct search subgraph

```mermaid
flowchart LR
    input[/"Validated search"/] --> call["Call MCP tool"]
    call --> outcome{"Tool outcome"}
    outcome -->|"Success"| normalize["Normalize"]
    outcome -->|"No results"| empty["Empty response"]
    outcome -->|"Transient"| retry{"Retry available?"}
    outcome -->|"Permanent"| failure["Provider error"]
    retry -->|"Yes"| call
    retry -->|"No"| failure
    normalize --> rank["Rank and limit"]
    rank --> save["Persist snapshot"]
    save --> output[/"Structured result"/]
    empty --> output
    failure --> output
```

No LLM participates in this path.

## Itinerary subgraph

After validation, flight, hotel, places, and weather searches fan out independently. Required branches are selected from the request; a local trip may not need flights, and a day trip may not need hotels.

```mermaid
flowchart TD
    input[/"Validated trip request"/] --> fanout["Select required searches"]
    fanout --> flights["Search flights"]
    fanout --> hotels["Search hotels"]
    fanout --> places["Search places"]
    fanout --> weather["Get weather"]
    flights --> aggregate["Aggregate results"]
    hotels --> aggregate
    places --> aggregate
    weather --> aggregate
    aggregate --> save["Persist search results"]
    save --> compact["Compact top results"]
    compact --> model["Synthesize itinerary"]
    model --> validate{"Output valid?"}
    validate -->|"Repair once"| model
    validate -->|"Yes"| persist["Persist itinerary"]
    validate -->|"No"| error["Return safe error"]
    persist --> output[/"Itinerary response"/]
    error --> output
```

The synthesis node receives only validated preferences and compact normalized evidence. It cannot invent booking availability; each option retains its source, observed time, currency, and offer identifier.

## Model gateway subgraph

```mermaid
flowchart TD
    select["Select profile and provider"] --> invoke["Invoke model"]
    invoke --> result{"Invocation result"}
    result -->|"Success"| validate["Validate output"]
    result -->|"Transient"| retry{"Retry available?"}
    result -->|"Unavailable"| fallback["Select fallback"]
    result -->|"Invalid request"| fail["Return model error"]
    result -->|"Safety refusal"| refusal["Return refusal"]
    retry -->|"Yes"| invoke
    retry -->|"No"| fallback
    fallback --> providers{"Provider remains?"}
    providers -->|"Yes"| invoke
    providers -->|"No"| unavailable["Service unavailable"]
    validate --> done(["Return output"])
    refusal --> done
    fail --> done
    unavailable --> done
```

Fallback must not run for malformed input, policy refusal, or an MCP/provider error. Each configured fallback model must support the same tool and structured-output contract.

## Interrupt and resume

Missing required fields produce an interrupt payload:

```json
{
  "type": "input.required",
  "request_id": "req_123",
  "fields": ["departure_date"],
  "message": "What date would you like to depart?"
}
```

FastAPI sends it over WebSocket. Flutter replies with the same `request_id`, and the graph resumes from its PostgreSQL checkpoint. Repeated resume payloads must be idempotent.

## Threading and concurrency

- `conversation_id` is the logical memory key.
- `request_id` is the invocation and idempotency key.
- MVP policy allows one active request per conversation.
- A newer request may explicitly cancel the active search before starting.
- Multiple conversations for one user may run independently.
- Checkpoint writes for a conversation must be serialized to prevent lost state.

## Bounded execution

Every graph run has hard limits:

- maximum model attempts;
- maximum provider fallbacks;
- maximum tool calls per request;
- maximum repair attempts;
- maximum compact results per domain;
- maximum wall-clock duration;
- per-user token and provider-call budget.

When a limit is reached, the graph returns a structured partial or failure result; it does not loop indefinitely.

## Checkpointing

Development may use an in-memory checkpointer. Production uses `AsyncPostgresSaver` with a separate `langgraph` schema. Checkpoints support interrupts and recovery, but normalized application tables remain the durable business source of truth.
