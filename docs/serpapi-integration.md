# SerpApi flight and hotel search

SerpApi provides Google Flights and Google Hotels search results. Roamly uses
these results for discovery and budget comparison; booking happens through the
external link where one is available. A search result is not a ticket, live
inventory guarantee, or final checkout price.

## Configuration

Set `SERPAPI_API_KEY`. Enable each search independently:

```env
FLIGHT_PROVIDER=serpapi
HOTEL_PROVIDER=serpapi
SERPAPI_API_KEY=...
```

Flight search also requires the application-owned airport directory and IANA
timezone metadata snapshots. Generate and validate them with the existing
`scripts/export_airport_datasets.py` and `scripts/validate_flight_snapshot.py`
tools, then configure `AIRPORT_DIRECTORY_PATH` and `FLIGHT_METADATA_PATH`.
These datasets provide airport identifiers and timezone metadata independently
of any search provider.

Hotel search needs a destination resolver: Google Places, or a configured
`WEATHER_API_KEY` for WeatherAPI location search.

## Search behavior

- Flight requests preserve route, travel dates, return dates, cabin, passenger
  ages, nonstop preference and currency. Round-trip results map outbound and
  return segments into the shared flight schema.
- Every flight returned in SerpApi's `best_flights` and `other_flights` arrays
  is normalized, sorted by price with unknown prices last, and returned up to
  the backend's 100-option limit. Prices unavailable from the provider remain
  null.
- Hotel search includes dates, guests, child ages, currency and optional free
  cancellation. With no budget, it returns up to 10 distinct hotels ordered by
  review rating, then review count. It keeps the cheapest known booking rate for
  each hotel.
- Users can give a maximum budget for the complete stay. The backend filters
  against each result's total stay price in the selected currency; unknown prices
  are excluded because they cannot be verified against the budget. A general trip
  budget is never treated as the accommodation budget.
- Standalone flight search includes the returned options up to 100. Hotel search
  returns up to 10 distinct options. Itinerary research intentionally uses a small
  subset to bound prompt size.
- Searches with more than one hotel room are rejected. SerpApi's documented
  Google Hotels parameters do not define room count, so silently treating a
  multi-room query as a one-room search would misstate prices.
- Provider timeouts, malformed responses and oversized bodies become safe typed
  errors. The API key stays server-side.

Flight results include entries in the successful SerpApi response arrays up to
the application cap; this does not mean all matching flights in Google's index.
Hotel search can expose pagination tokens, but the current adapter makes one
search request and returns at most 10 distinct hotels without fetching more pages.

## Validation

Tests use synthetic provider payloads and mocked HTTP. They do not make paid
requests. Before enabling production search, manually verify route/property
coverage, result links, currency and price behavior against the routes and
destinations Roamly users commonly search. Prices and provider links can change
after the search, and the user must confirm the final price with the booking site.

Run focused tests:

```bash
uv run pytest tests/unit/providers/serpapi tests/unit/test_config.py tests/integration/test_lifespan.py -q
```
