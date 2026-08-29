# Flutter Location Selection Contract

## Scope

Phase 1 uses explicit text search for trip origins and destinations. It does not
request the device's current location, perform reverse geocoding, or show a
location-permission screen.

Flutter calls FastAPI only. Google credentials and provider payloads remain on
the backend, and location resolution does not invoke LangGraph, an LLM, or MCP.

## Client flow

1. The user enters at least two non-whitespace characters.
2. Flutter submits the search after an explicit action, such as tapping Search
   or selecting the keyboard search action.
3. Flutter calls the authenticated resolution endpoint once.
4. Flutter renders the returned options without changing their identifiers or
   coordinates.
5. The user explicitly selects one option.
6. Flutter uses `canonical_name` as the visible trip field and retains the full
   selected object.
7. Flutter sends both values when creating or changing the trip endpoint.

Flutter MUST NOT call the provider-backed endpoint on every keystroke. A future
type-ahead experience may use a debounce and minimum-length policy, but it also
needs caching and rate limiting before release.

## Resolve request

```http
GET /api/v1/locations/resolve?query=London&limit=5
Authorization: Bearer <access-token>
```

Constraints:

- `query`: trimmed length from 2 through 120 characters;
- `limit`: from 1 through 5, default `5`;
- authentication is required.

Successful response:

```json
{
  "query": "London",
  "options": [
    {
      "provider": "google",
      "provider_location_id": "london-provider-id",
      "canonical_name": "London, United Kingdom",
      "country_code": "GB",
      "latitude": 51.5074,
      "longitude": -0.1278
    }
  ]
}
```

An empty `options` list is a successful search with no usable geographic match.
It is not a transport failure.

## Flutter state

Each origin and destination field should keep separate draft and selected
state:

```text
idle -> editing -> loading -> options -> selected
                      |          |
                      v          v
                    error     noResults
```

Recommended client model:

```text
LocationFieldState
├── queryText: String
├── selectedLocation: CanonicalLocation?
├── options: List<CanonicalLocation>
├── status: idle | editing | loading | options | selected | noResults | error
└── errorCode: String?
```

If the user edits `queryText` after selecting an option, Flutter MUST clear
`selectedLocation`. This prevents a London provider ID from being submitted
with visible text changed to Manchester.

## Create trip

After selecting both endpoints, Flutter uses each `canonical_name` as the
corresponding visible value:

```http
POST /api/v1/trips
Authorization: Bearer <access-token>
Content-Type: application/json
```

```json
{
  "title": "London museums",
  "origin": "Lahore, Punjab, Pakistan",
  "destination": "London, United Kingdom",
  "origin_location": {
    "provider": "google",
    "provider_location_id": "lahore-provider-id",
    "canonical_name": "Lahore, Punjab, Pakistan",
    "country_code": "PK",
    "latitude": 31.5204,
    "longitude": 74.3587
  },
  "destination_location": {
    "provider": "google",
    "provider_location_id": "london-provider-id",
    "canonical_name": "London, United Kingdom",
    "country_code": "GB",
    "latitude": 51.5074,
    "longitude": -0.1278
  },
  "start_date": "2026-09-10",
  "end_date": "2026-09-15"
}
```

`origin` remains optional in the backend contract. If Flutter supplies
`origin_location`, it MUST also supply `origin`.

## Update trip

When the user chooses a different destination, Flutter sends the text and
canonical object together:

```http
PATCH /api/v1/trips/<trip-id>
Authorization: Bearer <access-token>
Content-Type: application/json
```

```json
{
  "destination": "Paris, France",
  "destination_location": {
    "provider": "google",
    "provider_location_id": "paris-provider-id",
    "canonical_name": "Paris, France",
    "country_code": "FR",
    "latitude": 48.8566,
    "longitude": 2.3522
  }
}
```

Sending new free text without a selected canonical object intentionally clears
stale provider metadata. Flutter should normally require selection before
enabling Save, so later flight, hotel, weather, and places searches use a stable
location.

## Error handling

| HTTP status | Flutter behavior |
| --- | --- |
| `200` with options | Show selectable results. |
| `200` with empty options | Show “No matching city or region found.” |
| `401` | Refresh the access token once, retry once, then sign in if refresh fails. |
| `422` | Keep the form open and display a query or trip validation message. |
| `503` | Preserve typed text and selection, then offer Retry. |

Flutter MUST NOT automatically retry `422`. Provider `503` retries should be
user initiated in Phase 1 so repeated calls cannot unexpectedly consume quota.

## Privacy and storage

- Do not request GPS permission for this flow.
- Do not send device coordinates.
- Do not embed a Google API key in Flutter.
- Store the selected normalized object as trip data, not raw provider JSON.
- Treat provider location IDs as opaque strings.
