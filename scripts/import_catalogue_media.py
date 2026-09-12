"""Explicit, resumable local-photo upload and exact-slug catalogue attachment.

Run with ``uv run python -m scripts.import_catalogue_media --help``.
No infrastructure changes are made by this script. The bucket must already allow
public image delivery. Unmatched photos remain local and are listed in the report.
"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.config import Settings
from app.database.models.destination import (
    Destination,
    DestinationMedia,
    DestinationPlace,
    DestinationPlaceMedia,
    MediaAsset,
)
from app.database.session import create_cloud_sql_resources

BUCKET = "travel-assistant-505317-media"
INSTANCE = "travel-assistant-505317:asia-south1:free-trial-first-project"
NAME = re.compile(r"(cover|gallery-([0-9]{2,}))\.(avif|jpg|jpeg|png|webp)")
MIME = {
    "avif": "image/avif",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


@dataclass(frozen=True)
class Photo:
    relative: str
    destination: str
    place: str | None
    order: int
    sha256: str
    mime: str
    key: str
    size: int

    @property
    def url(self) -> str:
        # Stable delivery revision avoids responses cached before CORS rollout.
        return f"https://storage.googleapis.com/{BUCKET}/{self.key}?delivery=v1"


def scan(root: Path) -> list[Photo]:
    """Validate every recognized photo before any remote writes."""
    photos = []
    slots = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        parts = path.relative_to(root).parts
        valid = (len(parts) == 3 and parts[1] == "destination") or (
            len(parts) == 4 and parts[1] == "places"
        )
        if not valid:
            continue
        match = NAME.fullmatch(path.name)
        if not match:
            raise ValueError(f"Unnormalized photo: {path}")
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError(f"Symlink not allowed: {path}")
        if any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", p) for p in parts[:-1]):
            raise ValueError(f"Invalid slug: {path}")
        mime = MIME[match[3]]
        detected = subprocess.run(
            ["file", "--brief", "--mime-type", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if detected != mime:
            raise ValueError(f"MIME mismatch: {path}: {detected}, expected {mime}")
        order = 0 if match[1] == "cover" else int(match[2])
        if match[1] != "cover" and order == 0:
            raise ValueError(f"Gallery order must be positive: {path}")
        slot = (parts[:-1], order)
        if slot in slots:
            raise ValueError(f"Duplicate image slot: {path}")
        slots.add(slot)
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        photos.append(
            Photo(
                relative=str(path.relative_to(root)),
                destination=parts[0],
                place=parts[2] if len(parts) == 4 else None,
                order=order,
                sha256=checksum,
                mime=mime,
                size=path.stat().st_size,
                key=f"destinations/{'/'.join(parts[:-1])}/{checksum}.{match[3]}",
            )
        )
    return photos


async def upload(photo: Photo, root: Path, client: httpx.AsyncClient) -> None:
    """Upload immutable bytes; verify anonymous delivery before DB changes."""
    path = root / photo.relative
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != photo.sha256:
            raise ValueError(f"Photo changed after preflight: {path}")
    origin = "https://media-preview.example"
    existing = await client.head(photo.url, headers={"Origin": origin})
    if existing.status_code == 404:
        await copy_object(photo, path)
    else:
        existing.raise_for_status()
    async with client.stream("GET", photo.url, headers={"Origin": origin}) as response:
        response.raise_for_status()
        if response.headers.get("access-control-allow-origin") != "*":
            raise ValueError(f"Public CORS delivery missing: {photo.relative}")
        if response.headers.get("content-type", "").split(";")[0] != photo.mime:
            raise ValueError(f"Delivery MIME mismatch: {photo.relative}")
        checksum = hashlib.sha256()
        async for chunk in response.aiter_bytes():
            checksum.update(chunk)
        if checksum.hexdigest() != photo.sha256:
            raise ValueError(f"Delivery checksum mismatch: {photo.relative}")


async def copy_object(photo: Photo, path: Path) -> None:
    """Create a missing object with a bounded, non-overwriting CLI operation."""
    process = await asyncio.create_subprocess_exec(
        "gcloud",
        "storage",
        "cp",
        str(path),
        f"gs://{BUCKET}/{photo.key}",
        "--no-clobber",
        f"--content-type={photo.mime}",
        "--cache-control=public,max-age=31536000,immutable",
        "--quiet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(process.communicate(), timeout=120)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f"Upload failed for {photo.relative}; DB unchanged")


async def run(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    photos = scan(root)
    settings = Settings()
    if (
        settings.cloud_sql_instance_connection_name != INSTANCE
        or settings.database_name != "travel_assistant"
    ):
        raise ValueError("Configured DB is not the explicitly supported development DB")
    engine, connector = await create_cloud_sql_resources(settings)
    try:
        async with engine.connect() as db:
            destinations = {
                r.slug: (r.id, r.name)
                for r in (
                    await db.execute(
                        select(Destination.id, Destination.slug, Destination.name)
                    )
                )
            }
            places = {
                (r.destination_id, r.slug): (r.id, r.name)
                for r in (
                    await db.execute(
                        select(
                            DestinationPlace.id,
                            DestinationPlace.destination_id,
                            DestinationPlace.slug,
                            DestinationPlace.name,
                        )
                    )
                )
            }
        matched = []
        pending = []
        for photo in photos:
            destination = destinations.get(photo.destination)
            owner = (
                destination
                if photo.place is None
                else places.get(
                    (destination[0], photo.place)
                    if destination
                    else (None, photo.place)
                )
            )
            if owner:
                matched.append((photo, owner))
            else:
                pending.append(photo.relative)
        print(
            json.dumps(
                {
                    "photos": len(photos),
                    "matched": len(matched),
                    "pending_missing_records": len(pending),
                    "upload_bytes": sum(p.size for p, _ in matched),
                }
            )
        )
        if not args.apply:
            print("Read-only preflight complete; no uploads or DB writes.")
            return
        if not matched:
            raise ValueError("No matching catalogue records")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        report = root / f"bucket-import-{stamp}.json"
        snapshot = {}
        async with engine.connect() as db:
            for table in (
                MediaAsset.__table__,
                DestinationMedia.__table__,
                DestinationPlaceMedia.__table__,
            ):
                snapshot[table.name] = [
                    dict(r) for r in (await db.execute(select(table))).mappings()
                ]
        report_data = {
            "bucket": BUCKET,
            "before": snapshot,
            "planned": [asdict(p) for p, _ in matched],
            "pending": pending,
        }
        with report.open("x") as stream:
            json.dump(report_data, stream, default=str, indent=2)
        print(f"Recovery snapshot and pending list: {report}", flush=True)
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            for index, (photo, _) in enumerate(matched, 1):
                await upload(photo, root, client)
                print(f"Verified upload {index}/{len(matched)}", flush=True)
        # No transaction is held across uploads. A failed upload changes no DB rows.
        async with engine.begin() as db:
            await db.execute(text("SET LOCAL statement_timeout = '30s'"))
            await db.execute(text("SET LOCAL lock_timeout = '10s'"))
            await db.execute(text("SELECT pg_advisory_xact_lock(505317, 1)"))
            for photo, (owner_id, owner_name) in matched:
                asset_id = (
                    await db.execute(
                        insert(MediaAsset)
                        .values(
                            id=uuid4(),
                            storage_key=photo.key,
                            url=photo.url,
                            mime_type=photo.mime,
                            alt_text=f"{owner_name} — {'cover' if photo.order == 0 else 'gallery'}",
                            is_active=True,
                            license_info="User supplied; reuse rights not independently verified",
                        )
                        .on_conflict_do_update(
                            index_elements=[MediaAsset.storage_key],
                            set_={"url": photo.url, "is_active": True},
                        )
                        .returning(MediaAsset.id)
                    )
                ).scalar_one()
                model = (
                    DestinationMedia if photo.place is None else DestinationPlaceMedia
                )
                owner_column = (
                    "destination_id" if photo.place is None else "destination_place_id"
                )
                # Serialize with other writers on this owner's attachment rows.
                parent = Destination if photo.place is None else DestinationPlace
                found: UUID | None = (
                    await db.execute(
                        select(parent.id).where(parent.id == owner_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if found is None:
                    raise ValueError("Catalogue owner disappeared during upload")
                # Upsert the intended slot; old asset rows/objects are retained.
                await db.execute(
                    insert(model)
                    .values(
                        **{
                            owner_column: owner_id,
                            "media_asset_id": asset_id,
                            "role": "cover" if photo.order == 0 else "gallery",
                            "sort_order": photo.order,
                        }
                    )
                    .on_conflict_do_update(
                        index_elements=[getattr(model, owner_column), model.sort_order],
                        set_={
                            "media_asset_id": asset_id,
                            "role": "cover" if photo.order == 0 else "gallery",
                        },
                    )
                )
        print(
            f"Committed {len(matched)} attachments; {len(pending)} photos remain local pending catalogue records."
        )
    finally:
        try:
            await engine.dispose()
        finally:
            await connector.close_async()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))
