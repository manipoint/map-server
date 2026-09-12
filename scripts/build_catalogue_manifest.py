"""Build the reviewed, deterministic destination catalogue migration manifest.

This administrative helper reads the normalized local media collection and the
cached OpenStreetMap lookup result. It never connects to a database or bucket.
The generated JSON is reviewed and committed as immutable migration input.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from scripts.import_catalogue_media import BUCKET, Photo, scan

NAMESPACE = UUID("8a335936-6c2d-4b43-a2ba-346b96068ce5")
EXISTING = {
    "bali-indonesia",
    "bangkok-thailand",
    "dubai-united-arab-emirates",
    "hunza-pakistan",
    "istanbul-turkiye",
    "lahore-pakistan",
    "london-united-kingdom",
    "paris-france",
    "skardu-pakistan",
    "tokyo-japan",
}

# name, country name, ISO 3166-1 alpha-2, type, zoom, budget, styles, interests
DESTINATIONS = {
    "agra-india": (
        "Agra",
        "India",
        "IN",
        "city",
        11,
        "budget",
        ("culture", "food"),
        ("history", "local_culture", "photography"),
    ),
    "bali-indonesia": (
        "Bali",
        "Indonesia",
        "ID",
        "island",
        9,
        "mid_range",
        ("beaches", "culture", "nature"),
        ("local_culture", "photography", "wellness"),
    ),
    "bangkok-thailand": (
        "Bangkok",
        "Thailand",
        "TH",
        "city",
        11,
        "budget",
        ("culture", "food"),
        ("local_culture", "nightlife", "shopping"),
    ),
    "barcelona-spain": (
        "Barcelona",
        "Spain",
        "ES",
        "city",
        11,
        "premium",
        ("beaches", "culture", "food"),
        ("history", "nightlife", "photography"),
    ),
    "delhi-india": (
        "Delhi",
        "India",
        "IN",
        "city",
        10,
        "budget",
        ("culture", "food"),
        ("history", "local_culture", "shopping"),
    ),
    "dubai-united-arab-emirates": (
        "Dubai",
        "United Arab Emirates",
        "AE",
        "city",
        10,
        "premium",
        ("luxury", "food"),
        ("events", "nightlife", "shopping"),
    ),
    "goa-india": (
        "Goa",
        "India",
        "IN",
        "region",
        9,
        "mid_range",
        ("beaches", "culture", "nature"),
        ("history", "nightlife", "wellness"),
    ),
    "hunza-pakistan": (
        "Hunza",
        "Pakistan",
        "PK",
        "region",
        9,
        "mid_range",
        ("adventure", "nature"),
        ("hiking", "photography", "local_culture"),
    ),
    "interlaken-switzerland": (
        "Interlaken",
        "Switzerland",
        "CH",
        "city",
        11,
        "premium",
        ("adventure", "nature"),
        ("hiking", "photography", "wellness"),
    ),
    "istanbul-turkiye": (
        "Istanbul",
        "Türkiye",
        "TR",
        "city",
        11,
        "mid_range",
        ("culture", "food"),
        ("history", "local_culture", "shopping"),
    ),
    "kaghan-valley-pakistan": (
        "Kaghan Valley",
        "Pakistan",
        "PK",
        "region",
        8,
        "budget",
        ("adventure", "nature"),
        ("hiking", "photography", "wildlife"),
    ),
    "lahore-pakistan": (
        "Lahore",
        "Pakistan",
        "PK",
        "city",
        11,
        "budget",
        ("culture", "food"),
        ("history", "local_culture", "shopping"),
    ),
    "london-united-kingdom": (
        "London",
        "United Kingdom",
        "GB",
        "city",
        11,
        "premium",
        ("culture", "food"),
        ("events", "history", "shopping"),
    ),
    "lucerne-switzerland": (
        "Lucerne",
        "Switzerland",
        "CH",
        "city",
        11,
        "premium",
        ("culture", "nature"),
        ("history", "photography", "wellness"),
    ),
    "neelum-valley-pakistan": (
        "Neelum Valley",
        "Pakistan",
        "PK",
        "region",
        8,
        "budget",
        ("adventure", "nature"),
        ("hiking", "photography", "local_culture"),
    ),
    "new-york-united-states": (
        "New York City",
        "United States",
        "US",
        "city",
        11,
        "premium",
        ("culture", "food", "luxury"),
        ("events", "nightlife", "shopping"),
    ),
    "paris-france": (
        "Paris",
        "France",
        "FR",
        "city",
        11,
        "premium",
        ("culture", "food", "luxury"),
        ("history", "photography", "shopping"),
    ),
    "phuket-thailand": (
        "Phuket",
        "Thailand",
        "TH",
        "island",
        10,
        "mid_range",
        ("beaches", "nature", "luxury"),
        ("nightlife", "photography", "wellness"),
    ),
    "rome-italy": (
        "Rome",
        "Italy",
        "IT",
        "city",
        11,
        "premium",
        ("culture", "food"),
        ("history", "local_culture", "photography"),
    ),
    "singapore-singapore": (
        "Singapore",
        "Singapore",
        "SG",
        "country",
        11,
        "premium",
        ("culture", "food", "luxury"),
        ("events", "shopping", "local_culture"),
    ),
    "skardu-pakistan": (
        "Skardu",
        "Pakistan",
        "PK",
        "region",
        9,
        "mid_range",
        ("adventure", "nature"),
        ("hiking", "photography", "wildlife"),
    ),
    # ISO country metadata is used for application filtering; the display copy
    # remains neutral about the wider Kashmir territorial dispute.
    "srinagar-kashmir": (
        "Srinagar",
        "India",
        "IN",
        "city",
        10,
        "mid_range",
        ("culture", "nature"),
        ("history", "local_culture", "photography"),
    ),
    "swat-pakistan": (
        "Swat",
        "Pakistan",
        "PK",
        "region",
        8,
        "budget",
        ("adventure", "nature"),
        ("hiking", "photography", "local_culture"),
    ),
    "sydney-australia": (
        "Sydney",
        "Australia",
        "AU",
        "city",
        11,
        "premium",
        ("beaches", "culture", "food"),
        ("events", "photography", "wildlife"),
    ),
    "tokyo-japan": (
        "Tokyo",
        "Japan",
        "JP",
        "city",
        11,
        "premium",
        ("culture", "food"),
        ("events", "local_culture", "shopping"),
    ),
    "xian-china": (
        "Xi'an",
        "China",
        "CN",
        "city",
        11,
        "mid_range",
        ("culture", "food"),
        ("history", "local_culture", "photography"),
    ),
    "zurich-switzerland": (
        "Zurich",
        "Switzerland",
        "CH",
        "city",
        11,
        "premium",
        ("culture", "nature", "luxury"),
        ("history", "photography", "shopping"),
    ),
}

# Reviewed fallbacks for entries Nominatim did not resolve reliably. Coordinates
# are representative map points, not navigation-grade entrances.
FALLBACKS = {
    ("bali-indonesia", "tegalalang-rice-terraces"): (-8.4312, 115.2792),
    ("delhi-india", "humayuns-tomb"): (28.593278, 77.250694),
    ("kaghan-valley-pakistan", None): (34.9093, 73.6507),
    ("kaghan-valley-pakistan", "babusar-pass"): (35.1499, 74.0422),
    ("kaghan-valley-pakistan", "dudipatsar-lake"): (35.0189, 73.8017),
    ("kaghan-valley-pakistan", "saif-ul-malook-lake"): (34.8763, 73.6947),
    ("phuket-thailand", "promthep-cape"): (7.7593, 98.3036),
    ("skardu-pakistan", "katpana-cold-desert"): (35.310522, 75.590747),
    ("zurich-switzerland", "uetliberg"): (47.349444, 8.491389),
}

SPECIAL_NAMES = {
    "al-fahidi": "Al Fahidi Historical Neighbourhood",
    "hohematte-park": "Höhematte Park",
    "humayuns-tomb": "Humayun's Tomb",
    "saif-ul-malook-lake": "Lake Saif-ul-Malook",
    "senso-ji": "Sensō-ji",
    "tegalalang-rice-terraces": "Tegallalang Rice Terraces",
    "uetliberg": "Uetliberg",
    "xian-city-wall": "Xi'an City Wall",
}


def stable_id(kind: str, key: str) -> str:
    return str(uuid5(NAMESPACE, f"{kind}:{key}"))


def place_name(slug: str) -> str:
    return SPECIAL_NAMES.get(slug, slug.replace("-", " ").title())


def place_type(slug: str) -> str:
    for token, value in (
        ("lake", "lake"),
        ("beach", "beach"),
        ("fort", "fort"),
        ("palace", "palace"),
        ("garden", "garden"),
        ("park", "park"),
        ("mosque", "religious_site"),
        ("shrine", "religious_site"),
        ("temple", "religious_site"),
        ("pagoda", "religious_site"),
        ("museum", "museum"),
        ("market", "market"),
        ("bridge", "landmark"),
        ("tower", "landmark"),
        ("wall", "historic_site"),
    ):
        if token in slug:
            return value
    return "attraction"


def coordinates(item: dict) -> tuple[float, float, str, str]:
    key = (item["destination"], item["place"])
    match = item["match"]
    if match is not None:
        source_url = (
            f"https://www.openstreetmap.org/{match['osm_type']}/{match['osm_id']}"
        )
        return (
            float(match["lat"]),
            float(match["lon"]),
            source_url,
            match["display_name"],
        )
    lat, lon = FALLBACKS[key]
    return (
        lat,
        lon,
        "https://www.openstreetmap.org/copyright",
        "Reviewed representative map point",
    )


def media_record(photo: Photo, owner_name: str) -> dict:
    return {
        "id": stable_id("media", photo.key),
        "storage_key": photo.key,
        "url": photo.url,
        "mime_type": photo.mime,
        "alt_text": f"{owner_name} — {'cover' if photo.order == 0 else 'gallery image'}",
        "license_info": "User supplied; reuse rights not independently verified",
        "role": "cover" if photo.order == 0 else "gallery",
        "sort_order": photo.order,
        "sha256": photo.sha256,
        "size": photo.size,
    }


def unique_owner_photos(photos: list[Photo]) -> list[Photo]:
    """Keep the first slot for identical bytes within one owner gallery."""

    unique = []
    checksums = set()
    for photo in sorted(photos, key=lambda item: item.order):
        if photo.sha256 in checksums:
            continue
        checksums.add(photo.sha256)
        unique.append(photo)
    return unique


def build(root: Path, geocoding: Path) -> dict:
    lookup_doc = json.loads(geocoding.read_text())
    lookup = {(x["destination"], x["place"]): x for x in lookup_doc["items"]}
    photos = scan(root)
    by_owner: dict[tuple[str, str | None], list[Photo]] = {}
    for photo in photos:
        by_owner.setdefault((photo.destination, photo.place), []).append(photo)

    populated = {photo.destination for photo in photos}
    if populated != set(DESTINATIONS):
        raise ValueError(
            f"Destination mapping mismatch: {populated ^ set(DESTINATIONS)}"
        )

    destinations = []
    places = []
    media = []
    rank = 11
    for slug in sorted(populated):
        name, country, code, kind, zoom, budget, styles, interests = DESTINATIONS[slug]
        lat, lon, source_url, source_label = coordinates(lookup[(slug, None)])
        if slug not in EXISTING:
            destinations.append(
                {
                    "id": stable_id("destination", slug),
                    "slug": slug,
                    "name": name,
                    "destination_type": kind,
                    "country_name": country,
                    "country_code": code,
                    "summary": f"Explore {name} through its best-known landmarks, local culture, scenery, and travel experiences.",
                    "full_description": f"{name} is a curated {kind} destination in {country}. Use this guide to explore its major sights, local character, landscapes, and practical trip-planning highlights.",
                    "latitude": lat,
                    "longitude": lon,
                    "map_zoom": zoom,
                    "budget_tier": budget,
                    "is_published": True,
                    "is_featured": False,
                    "featured_rank": None,
                    "is_popular": False,
                    "popular_rank": None,
                    "editorial_rank": rank,
                    "styles": styles,
                    "interests": interests,
                    "source_url": source_url,
                    "source_label": source_label,
                }
            )
            rank += 1
            for photo in unique_owner_photos(by_owner[(slug, None)]):
                media.append(
                    {
                        **media_record(photo, name),
                        "owner_type": "destination",
                        "owner_id": stable_id("destination", slug),
                    }
                )

        place_slugs = sorted(
            place for dest, place in by_owner if dest == slug and place
        )
        if slug == "skardu-pakistan":
            continue
        for order, place_slug in enumerate(place_slugs, 1):
            owner_name = place_name(place_slug)
            lat, lon, source_url, source_label = coordinates(lookup[(slug, place_slug)])
            place_id = stable_id("place", f"{slug}/{place_slug}")
            places.append(
                {
                    "id": place_id,
                    "destination_slug": slug,
                    "slug": place_slug,
                    "name": owner_name,
                    "place_type": place_type(place_slug),
                    "summary": f"Visit {owner_name}, a notable stop in the {name} destination guide.",
                    "full_description": f"{owner_name} is included as a curated place to visit while exploring {name}. Check current access, opening conditions, and local guidance before travelling.",
                    "latitude": lat,
                    "longitude": lon,
                    "address": source_label[:300],
                    "sort_order": order,
                    "is_featured": order <= 2,
                    "is_published": True,
                    "source_url": source_url,
                }
            )
            for photo in unique_owner_photos(by_owner[(slug, place_slug)]):
                media.append(
                    {
                        **media_record(photo, owner_name),
                        "owner_type": "place",
                        "owner_id": place_id,
                    }
                )

    if (len(destinations), len(places), len(media)) != (17, 104, 224):
        raise ValueError(
            f"Unexpected manifest counts: {len(destinations), len(places), len(media)}"
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "coordinate_source": {
            k: lookup_doc[k]
            for k in ("provider", "attribution", "license", "generated_at")
        },
        "media_bucket": BUCKET,
        "destinations": destinations,
        "places": places,
        "media": media,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--geocoding", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    document = build(args.root.resolve(), args.geocoding.resolve())
    with args.output.open("x") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
