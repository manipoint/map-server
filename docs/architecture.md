# System Architecture

## Purpose

The system gives a Flutter client a single authenticated interface for travel search and itinerary planning while keeping provider credentials, LLM orchestration, persistence, and policy enforcement on the server.

The MVP is a modular monolith: FastAPI and FastMCP share one deployment boundary but retain separate code modules and contracts. This reduces infrastructure cost without coupling domain logic to transport code. The MCP module can later be deployed independently without changing its tool schemas.

## Runtime architecture

```mermaid
flowchart LR
    subgraph client ["Client"]
        flutter["Flutter App"]
    end

    subgraph gateway ["Public API Boundary"]
        fastapi["FastAPI REST and WebSocket"]
    end

    subgraph service ["Application Services"]
        auth["Auth and Session Service"]
        orchestrator["LangGraph Orchestrator"]
        mcp["Travel MCP Server"]
        persistence["Persistence Service"]
    end

    subgraph datastore ["Data Stores"]
        postgres[("PostgreSQL")]
    end

    subgraph external ["External Services"]
        llms["LLM Providers"]
        travel["Flight and Hotel APIs"]
        places["Places and Routes API"]
        weather["Weather API"]
        currency["Currency API"]
        langsmith["LangSmith"]
    end

    flutter <-->|"HTTPS and WSS"| fastapi
    fastapi -->|"Authenticates"| auth
    fastapi -->|"Runs requests"| orchestrator
    orchestrator -->|"Calls tools"| mcp
    orchestrator -->|"Saves outcomes"| persistence
    auth -->|"Reads sessions"| postgres
    persistence -->|"Reads and writes"| postgres
    orchestrator -.->|"Model inference"| llms
    orchestrator -.->|"Traces runs"| langsmith
    mcp -.->|"Searches offers"| travel
    mcp -.->|"Finds attractions"| places
    mcp -.->|"Gets conditions"| weather
    mcp -.->|"Converts prices"| currency
```

## Trust boundaries

### Flutter client

The client is untrusted. It MAY format and validate inputs for user experience, but the backend MUST repeat authorization and domain validation. Flutter never receives provider or LLM API keys.

### FastAPI public boundary

FastAPI owns:

- access-token verification and server-session checks;
- REST resource APIs and the authenticated WebSocket handshake;
- request size limits, rate limits, idempotency, and cancellation;
- mapping graph progress into versioned WebSocket events;
- authorization checks before reading or mutating user-owned resources.

### LangGraph orchestration

The graph owns workflow state and decisions. Structured Flutter events route directly to deterministic tool nodes. Natural-language requests use bounded model nodes only for extraction, clarification, or synthesis.

### MCP boundary

MCP exposes provider-independent, typed tools. It owns provider authentication, timeouts, response parsing, and normalization. It does not own user sessions, conversations, trips, or database transactions.

### PostgreSQL boundary

PostgreSQL stores normalized business data and LangGraph checkpoints in separate schemas. Repositories and services are the only application layers allowed to issue business-data queries.

## Primary request flow

```mermaid
sequenceDiagram
    title Structured travel search
    participant Flutter
    participant FastAPI
    participant LangGraph
    participant MCP
    participant Provider
    participant PostgreSQL

    Flutter->>FastAPI: travel.search event
    FastAPI->>FastAPI: Authenticate session
    FastAPI->>LangGraph: Start request
    LangGraph->>MCP: Call typed tool
    MCP->>Provider: Search request
    Provider-->>MCP: Provider payload
    MCP-->>LangGraph: Normalized results
    LangGraph->>PostgreSQL: Save search snapshot
    PostgreSQL-->>LangGraph: Search and offer IDs
    LangGraph-->>FastAPI: Completed state
    FastAPI-->>Flutter: search.completed event
```

## Deterministic versus agentic work

| Operation | Path |
| --- | --- |
| Flight/hotel form submission | Validate, call MCP, normalize, rank, persist, return. No LLM. |
| Weather or currency lookup | Direct MCP tool. No LLM. |
| Sorting, filtering, pagination | Backend code. No LLM. |
| Natural-language slot extraction | Economy model only if deterministic parsing is insufficient. |
| Multi-day personalized itinerary | Parallel MCP searches followed by one bounded synthesis call. |
| Saved trip retrieval | PostgreSQL query. No LLM unless the user asks for an explanation or modification. |

## Availability and degradation

- Provider timeouts are classified separately from model-provider failures.
- Partial travel results MAY be returned when one optional provider fails.
- Model providers use bounded retry, fallback, and circuit-breaking rules.
- No-results is a successful domain outcome, not a system exception.
- Search and model work MUST be cancellable when the user replaces an active request.
- Every side effect MUST use `request_id` as an idempotency key.

## Scaling path

### MVP

- one FastAPI/FastMCP deployment;
- one managed PostgreSQL database;
- one or more Uvicorn workers only when MCP/session behavior is configured for it;
- in-process bounded caches for non-critical data;
- direct LangSmith tracing with production sampling.

### Scale-out

Add Redis only when multiple backend instances require shared cache, WebSocket routing, circuit-breaker state, or distributed rate limiting. Split MCP into a separate deployment only when it needs independent scaling, security ownership, or reuse by multiple applications.

## Security requirements

- Production traffic uses HTTPS/WSS only.
- `/internal/mcp` is private or protected by service authentication.
- Provider and model keys come from a secret manager or injected environment variables.
- Logs and traces redact tokens, personal data, and provider payloads.
- Model reasoning is not sent to Flutter or persisted as a user-visible message.
- A logout or revoked session immediately invalidates active WebSocket connections.

## Out of scope for the MVP

- charging payment cards;
- purchasing flight or hotel inventory;
- cancellations and refunds;
- supplier reconciliation;
- affiliate accounting;
- multi-provider price arbitration beyond simple normalized comparison.
