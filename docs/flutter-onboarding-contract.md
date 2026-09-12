# Flutter Onboarding Contract

## Purpose

Preference onboarding runs after authentication and before the personalized home
screen. It stores durable user choices without invoking LangGraph, an LLM, MCP,
or a travel search provider. The only provider-backed operation is an explicit,
bounded canonical home-location lookup through the existing location endpoint.

## Routing flow

```text
authenticated
    -> GET /api/v1/users/me/preferences
        -> onboarding_completed=false -> onboarding
        -> onboarding_completed=true  -> home
```

Flutter should retain draft choices locally between onboarding pages and send one
final `PUT`. This avoids partial database writes and unnecessary network calls.
Login and session restoration perform one preference read. Home rebuilds must not
repeat it unless the user refreshes or edits preferences.

## Backend-owned options

Flutter MUST load active onboarding options from the backend and submit their
stable IDs. It may cache the last successful response for offline rendering.
Labels and ordering are presentation metadata; business logic uses IDs only.

```http
GET /api/v1/onboarding/options
```

The response contains a version plus ordered `travel_styles`, `interests`,
`budget_tiers`, `trip_paces`, and `recommendation_scopes`. Each option contains
`id`, `label`, `description`, `icon_key`, and `sort_order`. The endpoint performs
no provider call and returns public cache headers.

## Location and cost rules

- Budget never decides whether a trip is local or international.
- `recommendation_scope` explicitly carries `local`, `international`, or `both`.
- Local and international scopes require a user-selected canonical home location.
- Both scope allows no home location and falls back to general recommendations.
- Flutter must not request GPS permission for this flow.
- Flutter must not call location resolution on each keystroke.
- No preference endpoint calls an LLM, MCP, or an external provider.

## Read preferences

```http
GET /api/v1/users/me/preferences
Authorization: Bearer <access-token>
```

A new user receives `200`, not `404`:

```json
{
  "travel_styles": [],
  "interests": [],
  "budget_tier": null,
  "trip_pace": null,
  "recommendation_scope": "both",
  "home_location": null,
  "onboarding_completed": false,
  "personalization_ready": false,
  "onboarding_completed_at": null,
  "created_at": null,
  "updated_at": null
}
```

## Complete onboarding

Flutter resolves the home city through `GET /api/v1/locations/resolve` only after
an explicit search action and sends the selected canonical object unchanged.

```http
PUT /api/v1/users/me/preferences
Authorization: Bearer <access-token>
Content-Type: application/json
```

```json
{
  "travel_styles": ["nature", "adventure"],
  "interests": ["hiking", "history"],
  "budget_tier": "mid_range",
  "trip_pace": "balanced",
  "recommendation_scope": "local",
  "home_location": {
    "provider": "google",
    "provider_location_id": "lahore-provider-id",
    "canonical_name": "Lahore, Punjab, Pakistan",
    "country_code": "PK",
    "latitude": 31.5204,
    "longitude": 74.3587
  }
}
```

The operation is idempotent and replaces the complete preference selection in one
transaction. Travel styles accept one through three unique values. Interests
accept one through five unique values. The backend rejects unknown or duplicate
IDs even if Flutter has stale cached options.

## Skip onboarding

```http
POST /api/v1/users/me/onboarding/skip
Authorization: Bearer <access-token>
```

Skip marks onboarding complete but does not fabricate selections or erase choices
already stored. The response has `onboarding_completed=true` and may have
`personalization_ready=false`; Home must then render general discovery content.

## Recommendation boundary

These endpoints only collect inputs. Home and View All rank the curated catalogue
deterministically from all selected styles, interests, budget, and geographic
scope. They never call an LLM. LangGraph and MCP remain for explicit interactive
travel planning and live provider searches.
