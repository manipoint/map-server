# Travelport integration status

Last reviewed: 2026-09-30. Flight and airport-resolution tools are wired into
startup when Travelport is configured. One-way and round-trip journey searches
are implemented; live account inventory has not been certified. Hotel integration
is not implemented.

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

With `FLIGHT_PROVIDER=travelport`, startup loads the configured airport directory
and flight metadata and wires the auth client, search adapter, services and MCP
tools. Flight and metadata lookup share one bounded deadline. The adapter limits
response bytes, parses prices as Decimal, and allows one authentication recovery.
Hotel services remain unconfigured. Retired Duffel settings cannot reactivate it.

## Remaining integration work

- Verify live-account one-way and round-trip inventory using permitted sandbox
  searches. Mocked tests establish code behavior, not provider entitlement.
- Verify hotel access and implement a separate hotel adapter.
- Multi-city, split tickets and airport-transfer itineraries are unsupported.
- Booking, payment, ticketing, cancellation and refunds are outside this MVP.

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

The offline airport exporter writes a temporary manifest and renames it to
`manifest.json` only after all snapshot files and checksums are complete. This
prevents readers from observing a partially written final manifest. It does not
guarantee power-loss durability. Export failures attempt to remove only the new
snapshot; existing snapshots are preserved.

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

### Round-trip journey searches

Startup enables round trips and advertises flight research to the conversational
planner only when the flight service is configured. A return date produces two
search legs; it is never silently discarded.

`round_trip_mapper.py` joins fares only within the same content source and shared
`CombinabilityCode`. Travelport's `BestCombinablePrice` is the full journey price,
not a per-leg amount: matching prices are checked and counted once. See the
[Travelport Search API reference](https://support.travelport.com/webhelp/JSONAPIs/Airv11/Content/Air11/Search/APIRef_Search.htm).

Each leg reuses route, local-date, passenger-count, cabin and nonstop validation.
The return must depart after outbound arrival. Missing codes, unexpected catalog
routes/sequences, multiple products per leg and contradictory prices/currencies
fail closed. Unmatched codes or filtered legs produce no offers. Repeated codes
do not duplicate the same fare pair. Results retain at most `max_results` offers
and sort by combined price. All fares in a combinability group have one validated
price, so expansion stops after enough valid combinations for that group.

No independent one-way fares are added to manufacture a round-trip quote. No
split-payment or multi-city search is requested. Returned prices are search-time
observations for planning, not booking guarantees.

### Synthetic complete search flow

`tests/integration/test_travelport_one_way_flow.py` exercises the graph tool,
MCP client/server, airport resolution, flight service, authentication client,
response decoder and mapper together. Dataset loaders read the JSON fixtures
under `tests/fixtures/travelport`; only external HTTP is mocked. Coverage includes
one-way and round-trip offers with exact decimal pricing and cross-timezone UTC conversion,
empty inventory, ambiguous and unknown locations, token reuse and response
closure. These fixtures are not production datasets or live-provider evidence.

## Integration completeness

Implemented: OAuth/token handling, PCC configuration, one-way/round-trip search, normalized
flight mapping and metadata validation, MCP integration and mocked end-to-end
tests. This does not establish live-account inventory availability.

Not implemented: multi-city search, a Travelport hotel provider, offer
revalidation/booking, payment, ticketing, cancellation and refunds. The default
conversational planner does not replace round trips with one-way searches. It
reports missing flight/hotel evidence and can still generate an unverified draft.

### Opt-in live sandbox smoke test

Run from the repository root, with your existing `.env` configured for
`TRAVELPORT_ENVIRONMENT=preproduction`, `FLIGHT_PROVIDER=travelport`, Travelport
credentials/PCC and valid metadata paths. Use future departure/return dates:

```bash
uv run python -m scripts.smoke_travelport_round_trip \
  --run-sandbox \
  --origin JFK --destination LAX \
  --departure 2027-01-12 --return-date 2027-01-19 \
  --adults 1 --currency USD
```

This directly exercises the flight service, real auth/search clients, decoder,
metadata resolver and round-trip mapper. It does not start FastAPI or call an LLM,
MCP, or database. It does not book anything. Without `--run-sandbox`, it exits
before reading settings or calling the network. Production configuration is
rejected before HTTP client creation. Output is a small normalized price/count
summary; credentials, tokens, offer IDs and raw payloads are not printed or saved.

Exit codes: `0` means matching round-trip offers were mapped; `1` means provider,
response or execution failure; `2` means missing opt-in or invalid configuration/
input; `3` means inconclusive (no matching inventory or group guidance). An empty
sandbox catalog is not evidence that round-trip pricing works. Normal pytest runs
only mocked smoke-runner tests and never execute this live command.

Sandbox diagnostics write safe JSON events to stderr: `settings_load`,
`sandbox_configuration`, `metadata_load`, `flight_service`, `authentication`,
`flight_search`, and `response_validation`. HTTP events report only a locally
chosen stage, outcome and numeric status. `response_received` does not mean the
payload passed validation. A started request without a response status can mean
a transport failure or timeout. Authentication recovery appears as separate HTTP
attempts. No request URL, headers, response body or raw exception is logged.

For example, `authentication` with 401 indicates OAuth rejection;
`flight_search` with 403 indicates search access rejection. A 200 response followed
by failure still requires investigation of token/response validation or metadata
resolution; the script does not claim that every 200 is a successful search.

### Supplied AA/NDC baseline

To reproduce the supplied one-way request independently of the normalized mapper:

```bash
uv run python -m scripts.smoke_travelport_baseline --run-sandbox --departure 2026-10-30
```

Use a future date. This diagnostic deliberately targets the supplied sandbox PCC
`UM2_1G`, JFK to LAX, one adult, NDC, AA preferred, and four upsells. It does not
change `.env` or the production adapter. It validates the provider response with
the existing decoder, but does not certify normalized offers or round-trip pricing.
HTTP failures are not retried and raw payloads/tokens are not printed. Production
configuration is rejected. A successful baseline isolates access for this request;
it does not prove access to other content, PCCs, routes or workflows.
