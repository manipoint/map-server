# Destination Catalogue Contract

## Purpose

The destination catalogue is first-party editorial content. It powers Home cards,
paginated View All screens, destination details, map placement, and curated places
without invoking an LLM, MCP, or a live travel provider.

The catalogue distinguishes a destination from a place within it. For example,
Skardu is a destination while Upper Kachura Lake, Satpara Lake, Kharpocho Fort,
and Katpana Cold Desert are places associated with Skardu. A destination
coordinate is the map centre; each place has its own coordinate.

## Collections and View All

```http
GET /api/v1/destinations?collection=suggested&scope=both&limit=20
Authorization: Bearer <access-token>
```

`collection` accepts `suggested`, `popular`, or `featured`. `scope` applies only
to `suggested` and accepts `local`, `international`, or `both`. Results are
cursor-paginated and keep the same deterministic ordering used by Home.

Pagination uses a versioned seek cursor containing the last ordering key.
Filtering and ranking happen before the database page limit; all published,
eligible records are reachable, including records beyond the first 100.
Each View All query fetches at most `limit + 1` candidates.
Malformed or obsolete cursors return `422` with `error.code=invalid_cursor`.
Flutter should restart that list from its first page. Suggested cursors are bound
to the user and ranking preferences, so preference changes require a restart.
Removing the preceding page's last record does not invalidate continuation.
Editorial reordering during browsing is not a frozen snapshot: refresh the list
to see the new order and deduplicate cards by ID when appending pages.

```json
{
  "items": [
    {
      "id": "10000000-0000-4000-8000-000000000003",
      "slug": "skardu-pakistan",
      "name": "Skardu",
      "destination_type": "region",
      "country_name": "Pakistan",
      "country_code": "PK",
      "summary": "Plan an alpine escape around lakes, forts, and trails.",
      "cover_image": {
        "url": "https://media.example.com/destinations/skardu/cover.webp",
        "alt_text": "Lake and mountains near Skardu",
        "caption": null
      },
      "latitude": 35.2971,
      "longitude": 75.6333,
      "budget_tier": "mid_range",
      "styles": ["adventure", "nature"],
      "interests": ["hiking", "photography", "wildlife"]
    }
  ],
  "next_cursor": null
}
```

Flutter renders the returned order and MUST NOT reproduce recommendation scores.
The backend validates scope against the user's canonical home country. Local or
international scope without a home country returns an empty suggested page.

## Destination detail

```http
GET /api/v1/destinations/skardu-pakistan
Authorization: Bearer <access-token>
```

The response contains the destination overview, map centre, ordered gallery, and
a bounded preview of published places. The list-card cover appears in the gallery
only once.

```json
{
  "id": "10000000-0000-4000-8000-000000000003",
  "slug": "skardu-pakistan",
  "name": "Skardu",
  "destination_type": "region",
  "country_name": "Pakistan",
  "country_code": "PK",
  "summary": "Plan an alpine escape around lakes, forts, and trails.",
  "full_description": "Skardu is a mountain region used as a base for lakes, valleys, forts, and high-altitude landscapes.",
  "location": {"latitude": 35.2971, "longitude": 75.6333, "map_zoom": 9},
  "budget_tier": "mid_range",
  "styles": ["adventure", "nature"],
  "interests": ["hiking", "photography", "wildlife"],
  "gallery": [],
  "places": [],
  "places_next_cursor": null,
  "has_more_places": false
}
```

## Destination places

```http
GET /api/v1/destinations/skardu-pakistan/places?limit=20
GET /api/v1/destinations/skardu-pakistan/places/upper-kachura-lake
Authorization: Bearer <access-token>
```

Places are curated content, not live Google Places results. Each place contains a
stable slug, type, description, exact coordinate, optional address, cover image,
and ordered gallery. Place lists use cursor pagination.

Destination detail includes up to 10 place previews. When `has_more_places` is
true, pass `places_next_cursor` to the places endpoint to append the next page
without repeating the preview. To open a separate View All screen, start without
a cursor. Places have no total-100 cutoff.

The initial Skardu place rows are based on the Government of Gilgit-Baltistan
[Skardu tourism catalogue](https://visitgilgitbaltistan.gov.pk/district/id/3).
Kharpocho Fort coordinates use Pakistan's Department of Archaeology and Museums
[site record](https://doam.gov.pk/public/sites/7818). Image ownership and licence
metadata remain separate from destination facts.

## Media ownership

`media_assets` stores metadata and a public delivery URL. Legacy external images
may have a null `storage_key`; imported catalogue images use immutable bucket keys.

After the bucket rollout:

- image bytes live in object storage, never PostgreSQL;
- `storage_key` uses immutable names such as
  `destinations/<destination>/<owner>/<sha256>.<extension>`;
- `url` points to the public bucket or CDN delivery URL;
- destination and place join tables define cover/gallery role and order;
- only active media attached to published content is returned;
- alt text is required, while caption, credit, and licence metadata are optional;
- Flutter loads one cover per list card and the full gallery only on detail screens.

### Catalogue manifest and bucket import

The curated-image bucket is `gs://travel-assistant-505317-media` in `ASIA-SOUTH1`.
It uses uniform bucket access and anonymous `roles/storage.objectViewer` delivery;
public users cannot upload or delete objects. **Do not store private user media in
this bucket.** Public-access prevention must be inherited rather than enforced.
See [Google's public delivery instructions](https://docs.cloud.google.com/storage/docs/access-control/making-data-public).
`deploy/media-cors.json` allows cross-origin GET/HEAD for these public images,
including Flutter web; it grants no upload permission. Apply it only to this
public curated-image bucket with
`gcloud storage buckets update gs://travel-assistant-505317-media --cors-file=deploy/media-cors.json`.

The complete catalogue import is an immutable Alembic data migration. Build and
review its manifest, then preflight and upload only the referenced objects:

```bash
uv run python -m scripts.build_catalogue_manifest \
  --root /absolute/path/to/travel-media \
  --geocoding /absolute/path/to/catalogue-geocoding.json \
  --output /tmp/catalogue.json
uv run python -m scripts.upload_catalogue_manifest \
  --root /absolute/path/to/travel-media \
  --manifest alembic/data/f3a9c2d7e641_catalogue.json
uv run python -m scripts.upload_catalogue_manifest \
  --root /absolute/path/to/travel-media \
  --manifest alembic/data/f3a9c2d7e641_catalogue.json --apply
uv run alembic upgrade head
```

The upload command is read-only without `--apply` and never writes PostgreSQL.
Upload and public-delivery verification must finish before Alembic publishes the
rows. The migration then inserts destinations, places, tags, assets, and links in
one database transaction. Test upgrade, downgrade, and re-upgrade on an expiring
Neon branch before applying it to production with a direct, unpooled URL.

- The committed manifest contains deterministic UUIDs, exact parent-scoped slugs,
  coordinates with source provenance, and expected SHA-256/size metadata.
- Original formats are retained, with MIME validation; this is not an image
  optimization/conversion step. Verify AVIF rendering on target Flutter devices.
- Content-hashed object names are immutable and uploaded without overwriting;
  delivery responses use a one-year public cache lifetime.
- Every upload is fetched anonymously and its MIME and SHA-256 verified before
  DB writes. Public CORS delivery is also verified using an Origin header.
  No Google credentials are sent to Flutter. Delivery URLs use a stable
  `?delivery=v1` revision to bypass responses cached before CORS was configured;
  this is not an expiring signed URL and is not part of `storage_key`.
- Exact duplicate bytes within one owner gallery are omitted from the manifest;
  local source files are retained for review.
- Take and validate a full custom-format PostgreSQL backup before production
  migration. The downgrade deletes only deterministic records owned by this
  manifest; bucket objects are intentionally retained.
- If upload or transaction fails, already uploaded objects may remain unlinked;
  rerun the same batch. Do not delete such objects without checking DB references.
- User-provided images have unverified reuse rights recorded explicitly; retain
  source/attribution evidence and review rights and cover choices before release.

Bucket URLs are data in `media_assets.url`, so serving these existing contracts
does not require a storage SDK or per-request bucket calls in the backend.
The server must still run a release containing the destination catalogue routes
and media-table repository queries. Importing data does not deploy application
code; check deployed OpenAPI paths before Flutter integration.

## Query and cache boundaries

- Home returns at most six items per section.
- View All accepts 1 through 50 items and uses opaque cursors.
- Destination detail returns at most 12 gallery assets and 10 place previews.
- Place detail returns at most 12 gallery assets.
- Catalogue endpoints use private short-lived caching because Suggested depends on
  authenticated preferences.
- Repository queries remain bounded and avoid one query per destination or place.
