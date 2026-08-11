# Backend Structure

## Design goal

The backend is a modular monolith. Modules share one Python project and deployment while enforcing boundaries that allow later extraction into services. Transport, orchestration, provider integration, and persistence must remain independently testable.

## Target tree

```text
app/
├── main.py
├── config.py
├── lifespan.py
├── api/
│   ├── dependencies.py
│   ├── routes/
│   │   ├── auth.py
│   │   ├── trips.py
│   │   ├── conversations.py
│   │   └── health.py
│   └── websocket/
│       ├── travel.py
│       ├── connection_manager.py
│       └── events.py
├── auth/
│   ├── service.py
│   ├── tokens.py
│   ├── passwords.py
│   ├── sessions.py
│   └── schemas.py
├── graph/
│   ├── builder.py
│   ├── state.py
│   ├── routing.py
│   ├── nodes/
│   │   ├── input.py
│   │   ├── validation.py
│   │   ├── tools.py
│   │   ├── models.py
│   │   ├── persistence.py
│   │   └── responses.py
│   └── subgraphs/
│       ├── model_gateway.py
│       ├── flight_search.py
│       ├── hotel_search.py
│       └── itinerary.py
├── mcp/
│   ├── server.py
│   ├── client.py
│   ├── tools/
│   └── schemas/
├── providers/
│   ├── base.py
│   ├── flights/
│   ├── hotels/
│   ├── places/
│   ├── weather/
│   └── currency/
├── domain/
│   ├── enums.py
│   ├── errors.py
│   ├── flights.py
│   ├── hotels.py
│   ├── places.py
│   └── trips.py
├── services/
│   ├── search_service.py
│   ├── trip_service.py
│   ├── conversation_service.py
│   └── usage_service.py
├── database/
│   ├── base.py
│   ├── session.py
│   ├── models/
│   └── repositories/
├── observability/
│   ├── logging.py
│   ├── langsmith.py
│   └── metrics.py
└── common/
    ├── ids.py
    ├── time.py
    └── exceptions.py
```

Repository-level support:

```text
migrations/       # Alembic revisions
tests/unit/        # Pure domain and node tests
tests/contract/    # Provider and MCP schema fixtures
tests/integration/ # PostgreSQL, FastAPI, MCP, and graph tests
tests/evaluations/ # LangSmith datasets and quality checks
```

## Dependency direction

```mermaid
flowchart LR
    api["API and WebSocket"] --> services["Application Services"]
    api --> orchestrator["LangGraph"]
    orchestrator --> services
    orchestrator --> mcpClient["MCP Client"]
    services --> repositories["Repositories"]
    mcpClient --> mcpTools["MCP Tools"]
    mcpTools --> providers["Provider Adapters"]
    repositories --> database[("PostgreSQL")]
    api -.-> observability["Observability"]
    orchestrator -.-> observability
    mcpTools -.-> observability
    domain["Domain Schemas"] --> api
    domain --> orchestrator
    domain --> services
    domain --> mcpTools
```

The diagram describes compile-time dependency direction, not response direction.

## Module contracts

### `app.api`

May authenticate, validate transport envelopes, invoke services/graphs, and serialize responses. It must not call provider APIs, execute SQL directly, or construct model prompts.

### `app.auth`

Owns password verification, access-token issuance, refresh-token rotation, device sessions, reuse detection, and revocation. It exposes application services rather than framework middleware details.

### `app.graph`

Owns `TravelGraphState`, graph construction, conditional routing, interrupts, deterministic tool orchestration, bounded model calls, and final-response validation. Nodes call services through explicit dependencies; they do not import FastAPI request or WebSocket objects.

### `app.mcp`

`server.py` registers curated travel tools. `client.py` provides the internal graph-facing client. Tool schemas are stable contracts and are versioned when breaking changes are unavoidable.

### `app.providers`

Each provider adapter implements a domain interface. It handles provider authentication, HTTP behavior, rate-limit headers, response parsing, and mapping provider errors into domain errors. Provider payload types never leak into API or graph state.

### `app.domain`

Contains framework-independent Pydantic models, enums, value objects, and domain exceptions. It does not import FastAPI, SQLAlchemy, LangChain, or concrete provider clients.

### `app.services`

Owns business use cases and transaction boundaries: create search, save bounded offer snapshots, build/update trips, append messages, and record usage. Services coordinate repositories but do not know WebSocket event shapes.

### `app.database`

Owns async engine/session creation, SQLAlchemy models, repository implementations, and migrations. Models reflect normalized storage; repositories return domain objects or explicit persistence DTOs.

### `app.observability`

Provides structured logging, LangSmith metadata, metrics, redaction, and correlation helpers. Observability must not change business results when its external destination is unavailable.

## Configuration

`config.py` will load typed settings once at application startup. Categories include:

- environment and logging;
- public API and allowed origins;
- database pool and timeout settings;
- access/session token configuration;
- MCP URL and service authentication;
- provider endpoints, keys, timeouts, and limits;
- model profiles and fallback order;
- LangSmith tracing and sampling;
- retention, maximum result counts, and cost budgets.

Secrets must use secret types and must never appear in `repr`, logs, validation errors, health endpoints, or traces.

## Lifespan ownership

`lifespan.py` creates and closes long-lived resources:

1. typed settings;
2. database engine and pool;
3. provider HTTP clients;
4. MCP ASGI application/client;
5. LangGraph checkpointer and compiled graph;
6. WebSocket connection manager;
7. observability exporters.

Request handlers must reuse these clients instead of creating a new HTTP or database client per request.

## Error boundaries

Each lower layer raises typed errors:

```text
ProviderError
├── ProviderRateLimited
├── ProviderUnavailable
├── ProviderAuthenticationFailed
├── OfferExpired
└── LocationNotFound

ModelGatewayError
├── ModelTemporarilyUnavailable
├── ModelQuotaExhausted
├── ModelInvalidRequest
└── ModelOutputInvalid
```

API and graph response nodes map these errors into stable client error codes. Raw exception strings are logged with redaction but not returned to Flutter.

## Import rules

- No circular imports.
- Route modules may import services and graph entry points, not repository implementations.
- Graph nodes may import domain schemas and service protocols, not FastAPI.
- MCP tools may import provider interfaces and domain schemas, not application repositories.
- Provider adapters may import domain types, never graph or API modules.
- SQLAlchemy models remain inside `database` and are not WebSocket response schemas.

These rules should eventually be enforced with architecture tests or a dependency linter.
