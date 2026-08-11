# Database Design

## Purpose

PostgreSQL is the system of record for users, authentication sessions, conversations, trips, normalized provider results, itineraries, and usage records. LangGraph checkpoints use a separate schema so workflow persistence does not become coupled to product tables.

The design targets third normal form for durable business data. Provider payloads may also be retained temporarily as JSONB for debugging and reconciliation, but they are not the primary query model.

## Schemas

| Schema | Responsibility |
| --- | --- |
| `app` | Product, authentication, trip, search, and usage tables |
| `langgraph` | LangGraph checkpoints and workflow state |
| `audit` | Optional append-only security and administrative events |

Application roles should receive only the privileges required for their schema. Migration credentials should not be used by the running API.

## Identity and conversation model

```mermaid
erDiagram
    USER ||--o{ AUTH_SESSION : owns
    USER ||--o{ CONVERSATION : starts
    USER ||--o{ TRIP : plans
    CONVERSATION ||--o{ MESSAGE : contains
    CONVERSATION o|--o| TRIP : develops
    TRIP ||--o{ SEARCH_REQUEST : triggers

    USER {
        uuid id PK
        string email UK
        string password_hash
        string status
        datetime created_at
        datetime updated_at
    }
    AUTH_SESSION {
        uuid id PK
        uuid user_id FK
        string refresh_token_hash UK
        string device_id
        datetime expires_at
        datetime revoked_at
        datetime created_at
    }
    CONVERSATION {
        uuid id PK
        uuid user_id FK
        string title
        datetime created_at
        datetime updated_at
    }
    MESSAGE {
        uuid id PK
        uuid conversation_id FK
        string role
        text content
        jsonb metadata
        datetime created_at
    }
    TRIP {
        uuid id PK
        uuid user_id FK
        string destination
        date start_date
        date end_date
        string currency
        string status
        datetime created_at
    }
    SEARCH_REQUEST {
        uuid id PK
        uuid trip_id FK
        string search_type
        string status
        jsonb criteria
        datetime created_at
        datetime completed_at
    }
```

## Travel result model

Search results are snapshots. A price must always include its currency, provider, capture time, and applicable conditions because external availability can change immediately.

```mermaid
erDiagram
    TRIP ||--o{ FLIGHT_OFFER : considers
    FLIGHT_OFFER ||--|{ FLIGHT_SEGMENT : contains
    TRIP ||--o{ HOTEL_OFFER : considers
    HOTEL ||--o{ HOTEL_OFFER : priced_as
    TRIP ||--o{ TRIP_PLACE : includes
    PLACE ||--o{ TRIP_PLACE : selected_as
    TRIP ||--o{ WEATHER_SNAPSHOT : observes
    TRIP ||--o| ITINERARY : produces
    ITINERARY ||--|{ ITINERARY_ITEM : schedules
    TRIP ||--o{ PROVIDER_CALL : incurs
    TRIP ||--o{ LLM_USAGE : incurs

    TRIP {
        uuid id PK
        uuid user_id FK
        string destination
        date start_date
        date end_date
        string currency
    }
    FLIGHT_OFFER {
        uuid id PK
        uuid trip_id FK
        string provider
        string provider_offer_id
        decimal total_amount
        string currency
        datetime expires_at
        datetime captured_at
    }
    FLIGHT_SEGMENT {
        uuid id PK
        uuid flight_offer_id FK
        string origin_code
        string destination_code
        datetime departure_at
        datetime arrival_at
        string carrier_code
        string flight_number
    }
    HOTEL {
        uuid id PK
        string provider
        string provider_hotel_id
        string name
        decimal latitude
        decimal longitude
        string city
        string country_code
    }
    HOTEL_OFFER {
        uuid id PK
        uuid trip_id FK
        uuid hotel_id FK
        decimal total_amount
        string currency
        date check_in
        date check_out
        datetime captured_at
    }
    PLACE {
        uuid id PK
        string provider
        string provider_place_id
        string name
        string category
        decimal latitude
        decimal longitude
    }
    TRIP_PLACE {
        uuid id PK
        uuid trip_id FK
        uuid place_id FK
        integer priority
        integer planned_day
    }
    WEATHER_SNAPSHOT {
        uuid id PK
        uuid trip_id FK
        string location
        date forecast_date
        decimal temperature_c
        string condition
        datetime captured_at
    }
    ITINERARY {
        uuid id PK
        uuid trip_id FK
        integer version
        string status
        datetime created_at
    }
    ITINERARY_ITEM {
        uuid id PK
        uuid itinerary_id FK
        integer day_number
        integer position
        string item_type
        uuid source_id
        datetime starts_at
        datetime ends_at
    }
    PROVIDER_CALL {
        uuid id PK
        uuid trip_id FK
        string provider
        string operation
        string status
        integer latency_ms
        datetime created_at
    }
    LLM_USAGE {
        uuid id PK
        uuid trip_id FK
        string provider
        string model
        integer input_tokens
        integer output_tokens
        decimal estimated_cost
        datetime created_at
    }
```

## Important constraints

- `end_date` must be on or after `start_date`.
- Monetary amounts must be non-negative and paired with an ISO 4217 currency code.
- Flight arrival must be later than departure after timezone normalization.
- One provider entity is unique by `(provider, provider_*_id)`.
- Itinerary positions are unique within a day and itinerary version.
- Refresh tokens are stored only as hashes and are unique.
- Revoked or expired sessions cannot be refreshed.
- All timestamps are stored in UTC; source timezones remain available where travel display requires them.

## Index strategy

Create indexes from measured query patterns, starting with:

- `auth_session(refresh_token_hash)` and `(user_id, revoked_at)`.
- `conversation(user_id, updated_at desc)`.
- `message(conversation_id, created_at)`.
- `trip(user_id, created_at desc)`.
- `search_request(trip_id, search_type, created_at desc)`.
- Offer tables on `(trip_id, captured_at desc)`.
- `itinerary_item(itinerary_id, day_number, position)`.
- Usage tables on `(trip_id, created_at)` and `(provider, created_at)`.

Avoid indexing arbitrary JSONB until an observed query needs it.

## Transactions and idempotency

- Create a search request and its initial status in one transaction.
- Insert normalized provider results and mark the request complete atomically.
- Use a client-generated idempotency key for commands that may be retried.
- Keep network calls outside long-running database transactions.
- Use row-level locking or optimistic version checks when replacing an itinerary.

## Raw provider payloads

Raw responses can help diagnose parsing problems, but they may contain personal or commercially sensitive data. If retained:

- Store them in a separate table or object store with a short retention period.
- Encrypt them at rest.
- Redact credentials, payment data, and unnecessary traveler details.
- Associate them with `provider_call.id`, not with duplicated business columns.

## Migrations, backup, and retention

- Use Alembic migrations and review generated SQL before deployment.
- Run migrations as a release job, not independently in every API worker.
- Enable automated backups and point-in-time recovery in production.
- Test restoration regularly; an untested backup is not a recovery plan.
- Define retention separately for messages, provider payloads, usage records, and audit events.
- Delete or anonymize user data according to the product privacy policy.

## Connection management

Use an asynchronous PostgreSQL driver through SQLAlchemy, with a bounded application pool. Size the total connections as:

`instances × workers × pool size + operational reserve`

This total must remain below the managed database connection limit. Add a pooler such as PgBouncer when horizontal scaling makes direct connection counts inefficient.
