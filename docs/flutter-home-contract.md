# Flutter Home Discovery Contract

## Purpose

The Home endpoint returns personalized suggestions and curated discovery content
in one authenticated response. It reads PostgreSQL only. A Home refresh never
invokes LangGraph, an LLM, MCP, Google Places, Tavily, Duffel, or WeatherAPI.

## Request

```http
GET /api/v1/home?limit=6
Authorization: Bearer <access-token>
```

`limit` applies to every section and accepts one through six. The response uses
`Cache-Control: private, max-age=300`; Flutter should invalidate its cached Home
response after a successful preference update.

## Response

```json
{
  "personalization_ready": true,
  "suggested": [
    {
      "id": "10000000-0000-4000-8000-000000000002",
      "slug": "hunza-pakistan",
      "name": "Hunza",
      "destination_type": "region",
      "country_name": "Pakistan",
      "country_code": "PK",
      "summary": "Discover mountain valleys and dramatic landscapes.",
      "cover_image": {
        "url": "https://images.example.com/hunza.jpg",
        "alt_text": "Mountain valley in Hunza",
        "caption": null
      },
      "latitude": 36.3167,
      "longitude": 74.65,
      "budget_tier": "mid_range",
      "styles": ["adventure", "nature"],
      "interests": ["hiking", "photography", "wildlife"]
    }
  ],
  "suggested_local": [],
  "suggested_international": [],
  "popular": [],
  "spotlight": {
    "kind": "featured",
    "items": []
  }
}
```

Internal scores and editorial ranks are intentionally absent. Flutter renders
the order returned by the API and must not duplicate ranking rules.

Each section links to the corresponding cursor-paginated View All request. See
`destination-catalogue.md`. Home cards contain only a cover image; galleries are
loaded from destination or place detail endpoints.

`suggested` follows the user's saved recommendation scope. When a canonical home
country exists, `suggested_local` and `suggested_international` provide explicit
sections. A section outside the user's selected scope is empty.

## Deterministic suggestion rules

The backend calculates a stable score with fixed product weights:

| Match | Score |
| --- | ---: |
| Each selected travel style | 30 |
| Each selected interest | 12 |
| Exact budget tier | 20 |
| Adjacent budget tier | 10 |
| Two budget tiers away | 4 |

Ties use editorial rank and slug. `local` includes only the selected home
country, `international` excludes it, and `both` applies no country filter.
Budget never changes geographic scope.

If `personalization_ready` is false, `suggested` is empty while Popular and
Featured still provide useful general content.

## Popular, Featured, and Trending

Popular and Featured are explicit editorial flags and ranks stored with the
catalogue. They are available from the first release without paid search calls.

`spotlight.kind` is currently `featured`. It may become `trending` only after
first-party destination events, a rolling window, and minimum activity thresholds
are implemented. The service never labels Featured content as Trending.

## Query and cost boundary

Home reads preferences once, then runs two to five bounded collection queries,
depending on personalization and geographic scope. A local-only or
international-only selection reuses its Suggested result for that section.
All collections use the same SQL ranking as View All across the full published
catalogue. Only the requested cards are hydrated; there is no first-100 cutoff.
Tags are aggregated independently to avoid a styles-by-interests row product.
Queries sharing an async database session execute sequentially.

Without a home country, combined suggestions remain available and the two
geographic sections are empty. Flutter should offer home-location selection
instead of presenting these empty sections as a lack of available destinations.

Invalidate Home and Suggested View All caches after preference updates. Clear
user-specific caches on logout/account switching. Provider search and LLM
synthesis remain limited to explicit assistant/search actions.
