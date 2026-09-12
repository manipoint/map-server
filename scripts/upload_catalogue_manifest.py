"""Upload and verify only media referenced by an immutable catalogue manifest."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from scripts.import_catalogue_media import Photo, scan, upload


def select_photos(root: Path, manifest_path: Path) -> list[Photo]:
    """Resolve every manifest object to one checksum-matching local file."""

    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {row["storage_key"]: row for row in document["media"]}
    available: dict[str, Photo] = {}
    for photo in scan(root):
        if photo.key in expected:
            available.setdefault(photo.key, photo)
    missing = sorted(expected.keys() - available.keys())
    if missing:
        raise ValueError(f"Missing {len(missing)} manifest media files")
    for key, photo in available.items():
        row = expected[key]
        if photo.sha256 != row["sha256"] or photo.size != row["size"]:
            raise ValueError(f"Manifest media changed: {photo.relative}")
    return [available[key] for key in sorted(expected)]


async def apply_uploads(photos: list[Photo], root: Path, concurrency: int) -> None:
    """Upload with bounded concurrency and verify public bytes, MIME, and CORS."""

    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    lock = asyncio.Lock()
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:

        async def worker(photo: Photo) -> None:
            nonlocal completed
            async with semaphore:
                for attempt in range(3):
                    try:
                        await upload(photo, root, client)
                        break
                    except httpx.TransportError:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2**attempt)
            async with lock:
                completed += 1
                if completed % 10 == 0 or completed == len(photos):
                    print(f"Verified {completed}/{len(photos)} objects", flush=True)

        await asyncio.gather(*(worker(photo) for photo in photos))


async def run(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    photos = select_photos(root, args.manifest.resolve())
    print(
        json.dumps(
            {
                "objects": len(photos),
                "bytes": sum(photo.size for photo in photos),
                "apply": args.apply,
            }
        )
    )
    if not args.apply:
        print("Read-only preflight complete; no bucket writes.")
        return
    await apply_uploads(photos, root, args.concurrency)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=4)
    asyncio.run(run(parser.parse_args()))
