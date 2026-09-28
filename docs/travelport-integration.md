# Travelport integration status

Last reviewed: 2026-09-28. This document describes implemented components, not a
live search capability. Flight, hotel, and airport-resolution tools are still not
wired into application startup.

## Architecture

The intended flow is MCP tools -> shared search services -> provider contracts ->
Travelport adapter. Travelport authentication, request fields, references and
response parsing stay under `app/providers/travelport/`. Shared flight schemas,
`FlightProvider`, `FlightMetadataProvider`, and metadata models stay under
`app/providers/flights/`.

Replacing a provider should require a new adapter and startup selection, without
changing MCP or Flutter contracts. Flight, hotel and airport lookup adapters can
be selected independently. Provider switching does not imply automatic failover
or interchangeable offer identifiers.

## Implemented components

| Component | Current behavior |
| --- | --- |
| `auth_client.py`, `auth_schemas.py` | Form-encoded OAuth, in-process token cache, monotonic expiry margin, lock-serialized acquisition, stale-token invalidation, sanitized failures and authentication timeout. |
| `flight_request_schemas.py`, `flight_request_mapper.py` | NDC one-way/round-trip requests, passenger ages/categories, cabin/nonstop modifiers, requested currency, capped offers and no upsells. Groups over nine are rejected by the mapper. |
| `flight_response_schemas.py` | Errors/warnings, decimal prices/currency, local flight schedules, passenger cabin data, products, fare options, catalogs and flight/product reference sections. |
| `flight_response_decoder.py` | Checks provider errors before success fields, validates references, rejects duplicate IDs across sections and unresolved product/flight links. |
| `metadata_schemas.py`, `metadata_provider.py` | Validated airport IANA timezones, airline names, matching lookup keys and a provider-independent batch lookup contract. |
| `flight_metadata_resolver.py` | Collects codes from catalog-referenced products/flights, deduplicates them and makes one metadata lookup; empty catalogs skip lookup. |

The decoder retains warning-only responses. An explicit empty catalog is distinct
from a missing/malformed success catalog. Brand and terms reference sections are
not consumed yet; refundability, baggage and booking eligibility are not inferred.

## Metadata resolver policy

- Only catalog-referenced products and flights contribute lookup codes.
- Repeated fare/product references do not cause repeated provider calls.
- Missing any requested airport or airline fails the entire search with
  `ProviderUnavailableError`; partial-result filtering is not implemented.
- Provider exceptions and cancellation propagate; the resolver does not retry.
- The resolver has no independent timeout. A live provider/adapter must enforce a
  bounded lookup deadline before runtime wiring.
- Metadata dictionary keys must match embedded codes at model construction.
  Dictionaries remain mutable; consumers must not mutate validated lookup data.
- The resolver currently collects marketing carrier codes only.

The decoder validates links first. The resolver consumes that validated result
without rechecking every reference. Index lookups avoid repeated scans of all
flight/product definitions.

## Configuration and runtime

Preparatory settings are `TRAVELPORT_ENVIRONMENT`, `TRAVELPORT_USERNAME`,
`TRAVELPORT_PASSWORD`, `TRAVELPORT_CLIENT_ID`, `TRAVELPORT_CLIENT_SECRET`, and
`TRAVELPORT_PCC_CORE`. Credentials are secret values and must not be logged.
The auth client uses `provider_timeout_seconds`.

These settings do not activate flight search. `app/lifespan.py` still leaves
flight, airport, and hotel providers/services unconfigured. No live metadata
provider or Travelport search HTTP adapter has been implemented. Duffel code and
its live smoke scripts were removed; retired environment variables cannot
reactivate it.

## Remaining integration work

1. Choose and implement an authoritative airport/airline metadata source with a
   bounded batch lookup, resource ownership, and suitable caching.
2. Map airport-local schedules safely, including ambiguous/nonexistent local
   times. Do not assume UTC or infer journey duration by subtracting local times.
3. Resolve operating-carrier details. Do not assume the operating carrier equals
   the marketing carrier to satisfy required normalized fields.
4. Map and validate offers against the original request: route continuity, actual
   cabin/stops, traveler counts, currencies, offer identity and expiry.
5. Verify round-trip combinability and price semantics with representative
   fixtures. Do not blindly add `BestCombinablePrice` values or relabel returned
   prices with the requested currency. Parse HTTP JSON with `parse_float=Decimal`.
6. Implement the search HTTP adapter, safe error handling, bounded response size,
   timeout and restricted authentication recovery. Auth-lock serialization shares
   successful tokens, but concurrent failures can cause sequential auth attempts.
7. Translate oversized groups into the shared `GROUP_BOOKING_REQUIRED` result,
   then wire shared auth/HTTP resources, services and tools in lifespan.
8. Verify hotel access/integration separately; the flight trial proves neither
   hotel support nor production readiness.

## Verification

Run deterministic tests without live credentials or provider/model calls:

```bash
uv run pytest tests/unit/providers/travelport tests/unit/providers/flights -q
uv run ruff check app/providers/travelport app/providers/flights tests/unit/providers/travelport tests/unit/providers/flights
```

At this review, the focused suites passed **329 tests**, including eight resolver
cases for batch deduplication, unused references, empty catalogs, missing metadata,
provider failures, timeout propagation and cancellation. This is a scoped test
result, not a full-suite or live-provider certification.

The previously supplied saved NDC response decoded with one catalog offering,
125 products, 39 flights and warnings retained. That manual compatibility check
does not prove round-trip pricing or full NDC/GDS coverage. Automated tests use
synthetic payloads and do not depend on the developer's attachment directory.

### Startup dataset consistency

When Travelport is enabled, startup loads the airport directory and flight
metadata, then requires every directory airport to have validated timezone
metadata. A mismatch raises `ProviderConfigurationError` before Travelport
authentication-client construction or MCP registration. Existing lifespan cleanup
closes HTTP, connection-manager and database resources on this failure.

Metadata may contain additional airports for connections; equality between the
two datasets is not required. This check verifies code coverage, not geographic
correctness of timezone assignments or completeness for every possible carrier
and connecting airport. Response-time metadata validation remains necessary.

Regression tests cover matching coverage, additional metadata airports, missing
coverage (including empty metadata), file failures, cleanup and immutable code
inventories. No live Travelport calls are needed.

### Unsupported round-trip requests

Travelport startup configures `FlightSearchService(supports_round_trip=False)`.
Other service configurations retain the existing round-trip behavior by default.
After date and group-booking policy checks, unsupported round trips raise
`UnsupportedFlightRequestError` before airport resolution or provider search.
The Travelport adapter also guards direct calls with the same error.

MCP translates this into `FlightSearchGuidance` with status
`unsupported_request`, which the graph-facing client preserves. The message asks
for confirmation before an outbound-only search or separate-leg searches; the
return date is never silently removed. Graph and MCP descriptions explain this
handling without treating a feature limitation as a transient provider outage.
Tests exercise the graph-to-MCP-to-service path for one-way and round-trip
requests with the capability enabled and disabled, plus direct-service and
direct-adapter guards. Existing invalid-date and group guidance remain unchanged.
