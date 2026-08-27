# Travel Assistant MCP Backend

A Python backend for a Flutter travel-assistant application. The target system combines FastAPI, WebSockets, FastMCP, LangChain, LangGraph, LangSmith, PostgreSQL, and external travel providers to search flights, hotels, places, weather, and currency information and to build saved itineraries.

> **Project status:** the FastAPI foundation, Cloud SQL-capable asynchronous persistence, multi-device authentication, authenticated WebSocket chat, conversation persistence, assistant-run leases, trip persistence and its ownership-safe create/list/retrieve/update/plan/archive REST operations, a shared graph deadline, a bounded LangGraph tool loop, and ordered Groq → Google → OpenAI fallback are implemented and tested. In-process MCP tools currently support WeatherAPI weather, Duffel airport resolution, flights and hotels, Google Places, and Frankfurter currency conversion when configured. Permanent trip deletion, search snapshots, itinerary persistence, checkpoint/resume, REST conversation APIs, distributed WebSocket coordination, LangSmith instrumentation, and the remaining production reliability controls remain planned.

## Product scope

The first release is a search-and-planning assistant. It will:

- authenticate users and maintain revocable long-lived sessions;
- accept structured Flutter events and natural-language travel requests;
- search flights, hotels, attractions, weather, and exchange rates;
- stream progress and results to Flutter over WebSockets;
- persist conversations, searches, selected offers, and itineraries in PostgreSQL;
- use LLMs only when language understanding or itinerary synthesis adds value;
- fail over between configured LLM providers for eligible availability failures;
- trace quality, latency, token usage, and cost with LangSmith.

Booking, payment, cancellation, and refund workflows are intentionally deferred until the search-and-planning MVP is stable.

## Target architecture

```mermaid
flowchart LR
    flutter["Flutter App"] <-->|"HTTPS REST and WSS"| fastapi["FastAPI Boundary"]
    fastapi -->|"Auth and product data"| services["Application Services"]
    services -->|"Persist and query"| postgres[("PostgreSQL")]
    fastapi -->|"AI travel requests"| orchestrator["LangGraph Orchestrator"]
    orchestrator -->|"Typed tool calls"| mcp["In-process Travel MCP"]
    orchestrator -.->|"Model inference"| llms["LLM Providers"]
    orchestrator -.->|"Traces"| langsmith["LangSmith"]
    mcp -->|"Normalized searches"| providers["Travel Provider APIs"]
```

Flutter never receives provider credentials and does not connect directly to MCP. FastAPI services handle authentication, trips, conversations, and other durable product records through PostgreSQL. WebSocket AI requests may invoke LangGraph, which uses MCP only for typed external-provider operations.

## Current implementation

| File | Purpose |
| --- | --- |
| `app/config.py` | Typed settings loaded from environment variables. |
| `app/main.py` | FastAPI application factory and entry point. |
| `app/api/routes/health.py` | Process liveness endpoint. |
| `app/api/routes/auth.py` | Registration, login, token rotation, logout, and device-session endpoints. |
| `app/api/routes/trips.py` | Ownership-safe trip creation, listing, retrieval, updates, and lifecycle actions. |
| `app/auth/` | Password hashing, tokens, authentication services, schemas, and domain errors. |
| `app/database/` | Async SQLAlchemy sessions plus user, session, conversation, assistant-run, and trip persistence. |
| `app/services/conversation_service.py` | Idempotently persists conversations and user messages. |
| `app/services/conversation_processing_service.py` | Coordinates assistant-run leases and atomic reply persistence. |
| `app/services/travel_response_service.py` | Orchestrates cached replies, graph execution, retries, and safe failures. |
| `app/graph/` | Bounded model/tool loop, travel tools, prompts, response validation, and ordered model fallback. |
| `app/mcp/` | In-process FastMCP server, typed tools and schemas, and graph-facing client. |
| `app/providers/` | WeatherAPI, Duffel, Google Places, and Frankfurter adapters; see provider status below. |
| `app/api/websocket/` | Authenticated `/ws/travel` protocol, background response tasks, and event schemas. |
| `alembic/` | Migrations for users, authentication sessions, conversations, messages, assistant runs, and trips. |
| `app/observability/logging.py` | Structured JSON logging and sensitive-field redaction. |
| `tests/` | Unit and integration tests for implemented behavior. |

### Run the backend

Requirements:

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)

Install dependencies:

```bash
uv sync
```

Create a local `.env` file. Never commit real credentials:

```env
APP_ENV=local
APP_DEBUG=true
DATABASE_URL=postgresql+asyncpg://travel_user:replace-me@localhost:5432/travel_db
JWT_SIGNING_KEY=replace-with-a-long-random-value
TRAVEL_RESPONSE_TIMEOUT_SECONDS=75
ASSISTANT_RUN_COMPLETION_MARGIN_SECONDS=15
ASSISTANT_RUN_LEASE_SECONDS=120
```

Run the quality checks:

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest
```

Start FastAPI:

```bash
uv run uvicorn app.main:app --reload
```

The liveness endpoint is available at `http://127.0.0.1:8000/health/live`.

### Current provider wiring

| Capability | Provider | Runtime status |
| --- | --- | --- |
| Current weather | WeatherAPI | Always initialized; `WEATHER_API_KEY` is therefore required by the current startup path. |
| Location resolution | WeatherAPI search | Used by Duffel hotels and Google Places. |
| Airport resolution | Duffel Places | Enabled with flights; direct IATA codes skip the provider lookup. |
| Flights | Duffel | Enabled by `FLIGHT_PROVIDER=duffel`; city/airport names are resolved inside one flight-tool call. |
| Hotels | Duffel | Enabled by `HOTEL_PROVIDER=duffel`. |
| Places | Google Places | Enabled by `PLACES_PROVIDER=google`. |
| Currency | Frankfurter | Enabled by `CURRENCY_PROVIDER=frankfurter`. |

MCP is currently an internal Python boundary: `TravelMcpClient` calls the in-process FastMCP server object. No `/internal/mcp` HTTP route is mounted yet.

## Target backend modules

```text
app/
├── api/              # REST routes and WebSocket transport
├── auth/             # Login, token rotation, and session revocation
├── graph/            # LangGraph state, nodes, edges, and subgraphs
├── mcp/              # Travel MCP client, server, tools, and schemas
├── providers/        # External flight, hotel, places, weather, and FX adapters
├── domain/           # Provider-independent business models
├── services/         # Application use cases and transaction boundaries
├── database/         # SQLAlchemy models, repositories, and sessions
├── observability/    # LangSmith, structured logs, metrics, and cost tracking
└── common/           # Shared identifiers, time helpers, and exceptions
```

See [Backend Structure](docs/backend-structure.md) for ownership and dependency rules.

## Documentation

| Document | Description |
| --- | --- |
| [Documentation index](docs/README.md) | Reading order and document ownership. |
| [Phase 1 product scope](docs/phase-1-scope.md) | Included screens, deferred features, discovery rules, and implementation order. |
| [System architecture](docs/architecture.md) | Runtime boundaries, request flow, scaling, and security assumptions. |
| [Backend structure](docs/backend-structure.md) | Package layout and dependency direction. |
| [LangGraph design](docs/langgraph.md) | State, nodes, conditional edges, interrupts, and fallback subgraph. |
| [MCP server](docs/mcp-server.md) | Tool contracts, provider adapters, normalization, and error taxonomy. |
| [WebSocket protocol](docs/websocket-protocol.md) | Message envelope, events, reconnection, cancellation, and idempotency. |
| [Authentication](docs/authentication.md) | Access tokens, rotating sessions, device persistence, and logout. |
| [PostgreSQL model](docs/database.md) | Normalized schema, offer snapshots, retention, and indexes. |
| [Model routing and cost](docs/model-routing.md) | Multi-provider failover and token/cost controls. |
| [Deployment](docs/deployment.md) | Environments, process topology, secrets, health checks, and scaling. |
| [Testing](docs/testing.md) | Unit, contract, integration, graph evaluation, and load tests. |
| [Reliability and SPOF review](docs/reliability.md) | Current failure domains, mitigations, and production release gates. |
| [Development commands](docs/development-workflow.md) | Package management, formatting, tests, and local run commands. |

## Core engineering rules

1. Provider keys and model keys remain server-side and are loaded from environment variables or a secret manager.
2. Deterministic structured-search entry points should bypass the LLM; the currently exposed natural-language WebSocket flow uses the model to select MCP tools.
3. MCP tools normalize provider data but do not own business persistence.
4. PostgreSQL stores normalized business records; raw provider JSON is optional, short-lived evidence.
5. Saved prices are snapshots, not booking guarantees, and include `observed_at` and `expires_at`.
6. Model fallback is allowed for availability failures, never to bypass safety refusals or invalid input.
7. Internal model reasoning is neither sent to Flutter nor stored as conversation content.
8. Every request carries an idempotent `request_id` and every conversation has a stable `conversation_id`.

## Implementation order

| Phase | Status | Scope |
| --- | --- | --- |
| 1 | Complete | FastAPI shell, settings, health endpoints, middleware, and structured logging. |
| 2 | Complete | Async PostgreSQL, Cloud SQL connector, Alembic migrations, users, and revocable authentication sessions. |
| 3 | Complete | Versioned REST authentication API, authenticated WebSocket transport, and durable conversation messages. |
| 4 | In progress | Bounded LangGraph model/tool loop, shared graph deadline, and aligned assistant-run leases are complete; structured routing, interrupts, and checkpointing remain. |
| 5 | In progress | In-process FastMCP tools are complete; an authenticated mounted MCP transport is not implemented. |
| 6 | In progress | WeatherAPI, Duffel, Google Places, and Frankfurter adapters exist; provider failover and caching remain. |
| 7 | In progress | Ordered model gateway fallback and timeout controls are complete; LangSmith traces, budgets, circuit breaking, and evaluations remain. |
| 8 | Planned | Container deployment, monitoring, and load testing. |

## Security

- Never paste API keys into chat, tickets, logs, examples, or commits.
- Treat any exposed key as compromised and rotate it immediately.
- Use HTTPS/WSS in every non-local environment.
- Store refresh credentials only in platform-secure storage on Flutter and as hashes on the server.
- If an MCP HTTP transport is mounted later, restrict it to the backend network or protect it with service authentication.

## License

See [LICENSE](LICENSE).
