# Reliability and Single-Point-of-Failure Review

## Scope

This review describes the implemented repository as of 25 August 2026. It separates a **system SPOF**, whose failure makes the application broadly unavailable or risks durable data, from a **feature SPOF**, whose failure disables one or more travel capabilities while the rest of the service can continue.

This is an architecture risk register, not evidence that every listed failure has occurred. Infrastructure observations must be rechecked before each production release.

## Current failure domains

```mermaid
flowchart LR
    client["Flutter client"] --> api["FastAPI process"]
    api --> db[("PostgreSQL")]
    api --> graph["In-process LangGraph"]
    graph --> models["Groq, Google, OpenAI"]
    graph --> mcp["In-process FastMCP"]
    mcp --> weather["WeatherAPI"]
    mcp --> duffel["Duffel"]
    mcp --> places["Google Places"]
    mcp --> currency["Frankfurter"]
    weather --> location["Hotel and place location resolution"]
```

FastAPI, LangGraph, MCP, and the WebSocket connection registry share one process. This is an acceptable low-cost MVP boundary, but a process or container failure affects every API and travel feature on that instance.

## Risk register

| Priority | Failure point | Current impact | Existing protection | Required mitigation |
| --- | --- | --- | --- | --- |
| P0 | Production PostgreSQL/Cloud SQL is zonal or has backups disabled | Broad API/auth/chat outage; a storage incident can cause unrecoverable data loss. | Managed database and Alembic migrations. | Before production, use Cloud SQL high availability, automated backups, point-in-time recovery, alerts, and a tested restore procedure. |
| P0 | Database has no readiness gate | A process can report healthy and receive traffic while authentication and persistence are unusable. | `/health/live` proves only that the process responds. | Add `/health/ready` with a bounded `SELECT 1`; configure startup/readiness probes and keep liveness free of external calls. |
| Resolved | Assistant lease was shorter than the possible model/tool path | Previously, a slow run could be reclaimed while it was still executing, duplicating model/provider work and cost. | The complete graph now has a 75-second default deadline. Configuration requires the 120-second lease to exceed that deadline plus a 15-second persistence margin. Claim tokens and a unique assistant reply remain defense in depth. | Monitor deadline exhaustion. If legitimate workflows need longer execution, use lease renewal without removing the hard request deadline. |
| P0 | Model/tool work runs in a WebSocket-owned in-process task | Disconnect, restart, deploy, or process crash cancels work; the run remains processing until lease expiry. | Client idempotency and persisted run leases support a later retry. | Add durable job/outbox execution or a PostgreSQL LangGraph checkpointer with explicit resume semantics. Mark cancellation intentionally or shorten/renew the lease. |
| P1 | WebSocket registry and logout fan-out are process-local | In a multi-instance deployment, logout closes sockets only on the instance handling the request; another instance can retain an already-connected socket until it reauthenticates or disconnects. | Future REST/WS authentication checks consult the revoked database session. | Keep one instance until documented otherwise, or add Redis/pub/sub (or equivalent) for revocation broadcast and distributed connection ownership. |
| P1 | WeatherAPI is initialized unconditionally and also resolves locations | Missing credentials or an outage can block startup/current weather and can disable Duffel hotel or Google Places searches. | Typed provider errors and HTTP timeouts. | Make weather optional, separate geocoding from weather, add a location fallback/cache, and expose feature readiness independently. |
| P1 | Duffel is the only runtime provider for flights and hotels | One token, quota, provider outage, or contract change removes both high-value search features. | Provider-independent protocols and normalized schemas make replacement possible. | Add per-feature circuit breakers and flags; evaluate a second provider before availability commitments. Do not retry blindly. |
| P1 | Model fallback catches every ordinary exception | Invalid/configuration errors can trigger unnecessary fallback calls and cost. | Provider SDK retries are disabled, tool rounds are bounded, and the complete graph has one shared 75-second deadline. | Classify fallback-eligible errors, pass remaining time to each attempt, cap paid calls explicitly, and add per-provider circuit breakers and usage accounting. |
| P1 | No server-side rate limiting or cost quota | Login brute force, WebSocket floods, or prompt loops can exhaust DB, provider, and LLM capacity/cost. | Message-size, history, result, model-attempt, and tool-round bounds. | Add limits by IP, user, session, and operation; enforce daily model/provider budgets and return stable retry metadata. |
| P1 | Observability exporters are placeholders | Provider degradation, fallback storms, pool saturation, and cost growth may remain undetected. | Structured JSON access/application logs and request IDs. | Add metrics and alerts for DB pool, WebSockets, leases, provider errors/latency, model fallback/tokens/cost, and terminal graph outcomes. Add sampled LangSmith tracing with redaction. |
| P2 | One HS256 signing key has no key ID or overlap rotation | Rotation invalidates every access token; compromise affects the complete access-token trust boundary. | Short access-token lifetime and DB-backed session checks. | Support a key ring with `kid`, staged rotation, secret-manager versions, and a documented emergency procedure. |
| P2 | Frankfurter is the only currency source and results are not cached | Currency conversion alone becomes unavailable during its outage; repeated requests add avoidable latency. | Feature is optional and returns reference values, not payment quotes. | Add short TTL caching and graceful feature errors; add a second source only if the product availability target justifies it. |
| P2 | Per-instance SQLAlchemy pools multiply during scale-out | Cloud Run instance/worker growth can exhaust PostgreSQL connections and turn normal scaling into an outage. | Bounded pool size and overflow settings. | Set an instance cap from the DB connection budget, monitor pool waits, and add PgBouncer or another pooler when justified. |
| P2 | Saved trips, search snapshots, and provider evidence are not implemented | Users cannot retrieve a durable itinerary or useful search result after provider/model failure. | Conversations and assistant replies are persisted. | Implement normalized trip/search/itinerary tables and bounded evidence snapshots before promising durable trip planning. |

## Lease/deadline invariant

The current defaults are:

- model timeout: 30 seconds;
- configured model providers: up to 3;
- tool rounds: up to 2;
- shared graph timeout: 75 seconds;
- completion margin: 15 seconds;
- assistant-run lease: 120 seconds.

Without an outer deadline, one initial model invocation plus two post-tool invocations could each traverse three providers. The former theoretical model-only duration was:

```text
(1 initial invocation + 2 tool rounds) × 3 providers × 30 seconds = 270 seconds
```

Provider tool time could extend that path further. `TravelResponseService` now applies one timeout to the complete graph and atomic reply persistence. Configuration enforces the durable invariant:

```text
assistant_run_lease > response_end_to_end_deadline + failure_handling_margin
```

With defaults, `120 > 75 + 15`, leaving an additional 30 seconds beyond the required margin. A timeout cancels graph work and marks the owned run failed before its lease can be reclaimed. External client disconnect cancellation still deliberately leaves the lease for safe expiry, which is a separate durable-execution risk.

## Existing resilience strengths

- Groq, Google, and OpenAI provide model-vendor diversity when all are configured.
- Model SDK retries are disabled, preventing hidden nested retry multiplication.
- Tool rounds, model attempts, search result counts, message size, and conversation history are bounded.
- User-message idempotency, database leases, claim tokens, and the unique assistant-reply constraint prevent duplicate visible replies.
- Refresh tokens are hashed, rotated, replay-aware, and independently revocable per device.
- Provider adapters use typed normalized contracts and a shared bounded HTTP client.
- Structured logging redacts sensitive fields, and shutdown closes local WebSockets and clients.

These controls reduce damage, but they do not remove the SPOFs in the risk register.

## Production release gates

### Required before production data

1. Enable database HA, automated backups, point-in-time recovery, and alerts; complete a restore drill.
2. Add database readiness and platform startup/readiness probes.
3. Monitor graph timeout frequency and preserve the tested lease/deadline/margin invariant when changing configuration.
4. Add authentication, WebSocket, provider-call, and model-cost rate limits.
5. Add actionable metrics and alerts for the database, leases, providers, model fallback, and WebSockets.
6. Store every key in Secret Manager and document key/token rotation.

### Required before horizontal scale-out

1. Broadcast session revocation and WebSocket disconnects across instances.
2. Move long-running generation to durable execution or implement checkpoint/resume.
3. Size aggregate DB pools from the instance and worker limits.
4. Coordinate rate limits, caches, and circuit-breaker state where process-local state is insufficient.

### Feature-hardening follow-ups

1. Decouple location resolution from WeatherAPI and add bounded caches.
2. Add deterministic structured search endpoints so known fields do not require an LLM call.
3. Add saved trips, search snapshots, and itinerary persistence.
4. Decide availability targets per feature before paying for second travel providers.

## Verification checklist

- `GET /health/live` succeeds without external calls.
- `GET /health/ready` fails quickly when PostgreSQL is unavailable and recovers automatically.
- Killing a worker during generation produces a recoverable run, not permanent processing state.
- Revoking a session closes its sockets on every active instance.
- A provider timeout cannot exceed the graph deadline or assistant lease.
- A fallback storm cannot exceed per-request or per-user call/cost limits.
- Cloud SQL restoration and signing-key rotation have been exercised, not only documented.
- Optional provider failure degrades only its feature and does not prevent application startup.
