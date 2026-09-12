"""Offline preflight and delivery safety checks for the administrative importer."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts import import_catalogue_media as media
from scripts.build_catalogue_manifest import build


@pytest.fixture
def photo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        media.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="image/jpeg\n")
    )
    folder = tmp_path / "skardu-pakistan" / "destination"
    folder.mkdir(parents=True)
    (folder / "cover.jpg").write_bytes(b"test image")
    return tmp_path


def test_scan_stable_key_and_hidden_files(photo_root):
    (photo_root / "skardu-pakistan/destination/.DS_Store").write_bytes(b"ignored")
    photo = media.scan(photo_root)[0]
    assert photo.order == 0
    assert photo.place is None
    assert photo.mime == "image/jpeg"
    assert photo.sha256 in photo.key
    assert photo.url.startswith(
        f"https://storage.googleapis.com/{media.BUCKET}/destinations/"
    )
    assert media.scan(photo_root) == [photo]


def test_scan_rejects_mime_mismatch(photo_root, monkeypatch):
    monkeypatch.setattr(
        media.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="text/html\n")
    )
    with pytest.raises(ValueError, match="MIME mismatch"):
        media.scan(photo_root)


def test_scan_rejects_duplicate_slot(photo_root):
    (photo_root / "skardu-pakistan/destination/cover.jpeg").write_bytes(b"another")
    with pytest.raises(ValueError, match="Duplicate image slot"):
        media.scan(photo_root)


def test_scan_rejects_unrenamed_photo(photo_root):
    (photo_root / "skardu-pakistan/destination/photo-1.jpg").write_bytes(b"another")
    with pytest.raises(ValueError, match="Unnormalized"):
        media.scan(photo_root)


def test_scan_rejects_symlink(photo_root):
    folder = photo_root / "skardu-pakistan/destination"
    (folder / "gallery-01.jpg").symlink_to(folder / "cover.jpg")
    with pytest.raises(ValueError, match="Symlink"):
        media.scan(photo_root)


@pytest.mark.parametrize(
    "status,content,mime",
    [
        (403, b"denied", "image/jpeg"),
        (200, b"wrong bytes", "image/jpeg"),
        (200, b"test image", "text/html"),
    ],
)
def test_upload_rejects_bad_public_delivery(
    photo_root, monkeypatch, status, content, mime
):
    photo = media.scan(photo_root)[0]
    process = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(b"", b""))
    )
    monkeypatch.setattr(
        media.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )

    async def check():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                content=content,
                headers={"content-type": mime, "access-control-allow-origin": "*"},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                await media.upload(photo, photo_root, client)

    asyncio.run(check())


def test_changed_source_never_uploads(photo_root, monkeypatch):
    photo = media.scan(photo_root)[0]
    Path(photo_root / photo.relative).write_bytes(b"changed")
    execute = AsyncMock()
    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", execute)

    async def check():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="changed after preflight"):
                await media.upload(photo, photo_root, client)

    asyncio.run(check())
    execute.assert_not_called()


def test_existing_object_verified_without_reupload(photo_root, monkeypatch):
    photo = media.scan(photo_root)[0]
    execute = AsyncMock()
    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", execute)

    async def check():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"test image",
                headers={
                    "content-type": "image/jpeg",
                    "access-control-allow-origin": "*",
                },
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await media.upload(photo, photo_root, client)

    asyncio.run(check())
    execute.assert_not_called()


def test_failed_copy_stops_before_delivery(photo_root, monkeypatch):
    photo = media.scan(photo_root)[0]
    process = SimpleNamespace(
        returncode=1, communicate=AsyncMock(return_value=(b"", b""))
    )
    monkeypatch.setattr(
        media.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    with pytest.raises(RuntimeError, match="Upload failed"):
        asyncio.run(media.copy_object(photo, photo_root / photo.relative))


def test_missing_cors_rejected(photo_root):
    photo = media.scan(photo_root)[0]

    async def check():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"test image",
                headers={"content-type": "image/jpeg"},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="CORS"):
                await media.upload(photo, photo_root, client)

    asyncio.run(check())


def test_committed_catalogue_manifest_is_complete_and_consistent():
    root = Path("/Users/imranlatif/Develop/travel-media")
    geocoding = root / "catalogue-geocoding-20260913.json"
    if not root.exists() or not geocoding.exists():
        pytest.skip("Administrative source collection is not available")

    generated = json.loads(json.dumps(build(root, geocoding)))
    committed = json.loads(Path("alembic/data/f3a9c2d7e641_catalogue.json").read_text())
    generated.pop("generated_at")
    committed.pop("generated_at")
    assert generated == committed

    assert len(committed["destinations"]) == 17
    assert len(committed["places"]) == 104
    assert len(committed["media"]) == 224
    assert len({row["id"] for row in committed["media"]}) == 224
    assert len({row["storage_key"] for row in committed["media"]}) == 224

    covers = {}
    for row in committed["media"]:
        if row["role"] == "cover":
            covers.setdefault((row["owner_type"], row["owner_id"]), 0)
            covers[(row["owner_type"], row["owner_id"])] += 1
    assert set(covers.values()) == {1}
    assert len(covers) == 121
