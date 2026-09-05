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
      "country_name": "Pakistan",
      "country_code": "PK",
      "summary": "Discover mountain valleys and dramatic landscapes.",
      "image_url": "https://images.example.com/hunza.jpg",
      "image_alt": "Mountain valley in Hunza",
      "latitude": 36.3167,
      "longitude": 74.65,
      "budget_tier": "mid_range",
      "styles": ["adventure", "nature"],
      "interests": ["hiking", "photography", "wildlife"]
    }
  ],
  "popular": [],
  "spotlight": {
    "kind": "featured",
    "items": []
  }
}
```

Internal scores and editorial ranks are intentionally absent. Flutter renders
the order returned by the API and must not duplicate ranking rules.

## Deterministic suggestion rules

The backend calculates a stable score with fixed product weights:

| Match | Score |
| --- | ---: |
| Primary travel style | 40 |
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

Each request performs exactly two bounded logical reads:

1. one preference query;
2. one published-catalogue query capped at 100 candidates.

The catalogue query hydrates normalized styles and interests in the same SQL
round trip. Application code ranks the small candidate set in memory. Provider
search and LLM synthesis remain limited to explicit assistant/search actions.
