# Phase 1 Product Scope

## Purpose

This document is the product boundary for the first Flutter release. It describes target Phase 1 behavior; it does not claim that every listed capability is implemented. The README and code tests remain the source of truth for current implementation status.

The release is a travel search-and-planning product. It does not book travel or process payments.

## Included capabilities

| Area | Phase 1 behavior |
| --- | --- |
| Authentication | Email and password registration and login, token refresh, current-device logout, selected-device logout, logout from all devices, and active-session listing. |
| AI assistant | Authenticated WebSocket conversation with weather, airport, flight, hotel, places, and currency tools. |
| Trips | Create, list, retrieve, update, archive, and delete a user's own trips. |
| Location selection | Resolve ambiguous user text to a provider-qualified canonical location before persisting a trip. |
| Itineraries | Persist generated itineraries, retrieve them with their ordered items, and expose saved itineraries to Flutter. |
| Discovery | Database-backed Popular destinations and activity-derived Trending destinations when enough first-party data exists. |
| Profile | Basic account identity and security/session controls required by the implemented authentication system. |

## Deferred capabilities

The following design concepts are intentionally outside Phase 1:

- Google, Facebook, and Apple sign-in;
- email verification;
- phone-number login;
- password recovery and reset screens until the backend recovery flow exists;
- profile statistics and travel-preference management;
- booking, payment, cancellation, and refund workflows;
- bus and train search;
- public social feeds, reviews, likes, and popularity imported from third-party platforms.

Flutter MUST hide or omit deferred controls instead of presenting non-functional actions.

## Screen-to-backend boundary

| Flutter area | Backend source | Uses MCP or an LLM? |
| --- | --- | --- |
| Sign up and sign in | Authentication REST API | No |
| Active devices and logout | Authentication REST API and PostgreSQL | No |
| AI assistant | Authenticated WebSocket and LangGraph | Yes |
| Trips and trip history | Trip REST API and PostgreSQL | No |
| Trip location selection | Authenticated canonical-location REST API and Google Places | No LLM or MCP; one bounded provider request |
| Saved itinerary detail | Itinerary REST API and PostgreSQL | No |
| Popular destinations | Destination REST API and curated PostgreSQL records | No |
| Trending destinations | Aggregated first-party destination events | No |
| Live destination weather, places, hotels, and flights | Existing graph and MCP tools | Yes, only where live provider data or synthesis is required |

MCP is not the database API. FastAPI services and repositories own durable product data; MCP tools normalize calls to external travel providers.

## Trip lifecycle and history

The persisted trip status vocabulary is deliberately small:

- `draft`: planning has started but the itinerary is not finalized;
- `planned`: the trip has a usable saved plan;
- `archived`: the user intentionally removed the trip from normal active views.

Upcoming, active, and history are views derived from trip dates. They are not additional persisted statuses:

- upcoming: `start_date` is later than the selected current-date boundary;
- active: the selected current-date boundary is within the trip date range;
- history: `end_date` is before the selected current-date boundary.

The implementation MUST define a consistent user-date/timezone policy before adding these filters. A `cancelled` status is not needed while the product has no booking lifecycle.

## Popular, Featured, and Trending destinations

These labels have different meanings and MUST not be used interchangeably.

### Popular

Popular destinations are curated records stored in PostgreSQL. They provide deterministic, low-cost discovery from the first day of the product. The destination record should support at least a stable identifier, name, country, summary, image reference, publication state, and editorial ordering.

### Featured fallback

Featured destinations are curated records used when the service does not yet have enough trustworthy activity to calculate Trending. The API must tell Flutter which collection type it returned so the UI can display the correct heading.

### Trending

Trending is calculated only from the application's own recent activity. Proposed Phase 1 event weights are:

| Event | Weight |
| --- | ---: |
| Destination detail view | 1 |
| Destination search | 2 |
| Trip created | 4 |
| Itinerary saved | 5 |

The score uses a rolling seven-day window and should count distinct authenticated users so one user cannot dominate the list through repeated requests. Events require a durable destination identifier; free-text destination names are not an aggregation key.

The minimum activity and unique-user thresholds remain a product configuration decision. Until those thresholds are selected and met, the endpoint MUST return Featured results and label them as `featured`, not `trending`.

The initial event model needs:

- `destination_id`;
- `user_id`;
- `event_type`;
- `occurred_at`.

Trending computation is deterministic application/database logic. It must not spend LLM tokens or invoke MCP.

## Persistence milestones

Phase 1 adds these product areas in dependency order:

1. Trip persistence and ownership-safe REST operations.
2. Itinerary and ordered itinerary-item persistence linked to trips.
3. Destination catalogue records and curated Popular/Featured queries.
4. Destination event recording and the rolling Trending aggregation.
5. Flutter integration against stable REST and WebSocket contracts.

Search/provider snapshots may be added after trip persistence, but they are not required to render the first saved itinerary contract. Provider prices remain observations, never booking guarantees.

## Delivery gates

Each milestone is complete only when:

- schema constraints and indexes have migrations;
- repository queries enforce user ownership;
- service behavior has unit tests;
- public endpoints have authentication and integration tests;
- Alembic reports one head and no pending model changes;
- Ruff, formatting, and the complete test suite pass;
- current implementation status is updated in the README without presenting target behavior as finished.

## Current itinerary milestone

The itinerary persistence and REST slice is implemented:

1. versioned `draft`, `saved`, and `superseded` lifecycle contracts;
2. ordered itinerary items linked to trips with database cascade behavior;
3. unique versions, one saved version per trip, and unique daily positions;
4. ownership-safe repository queries and trip-row locking for version/save races;
5. transactional service validation and safe domain errors;
6. authenticated create, version-detail, current-saved, and save REST routes;
7. model, repository, service, schema, handler, dependency, and route tests.

The next integration milestone is to convert validated LangGraph itinerary output
into this service contract. Provider/model calls must finish before opening the
short persistence transaction.

Itinerary records store durable user selections and generated plans. Live provider payloads and prices remain timestamped observations rather than permanent booking guarantees.
