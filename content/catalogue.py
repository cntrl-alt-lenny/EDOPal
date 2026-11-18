"""Catalogue loading, offline fallback and per-pack display state."""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

import aiohttp

from network_tls import create_http_session

from .cache import CachedPack, PackCache
from .manifest import (
    DEFAULT_MANIFEST_URL,
    MANIFEST_URL_ENV,
    MAX_MANIFEST_BYTES,
    Manifest,
    ManifestError,
    Pack,
    is_compatible,
    parse_manifest,
    validate_url,
)

# Kept in Python so the offline snapshot survives a one-file build without a
# new data-file rule. The initial assets are awaiting redistribution review.
_SNAPSHOT = '{"schema_version":1,"packs":[]}'


def bundled_manifest() -> Manifest:
    """Load the embedded catalogue without network or disk access."""
    return parse_manifest(_SNAPSHOT)


class CatalogueSource(StrEnum):
    LIVE = "live"
    CACHED = "cached"
    BUNDLED = "bundled"


class PackState(StrEnum):
    NOT_DOWNLOADED = "not-downloaded"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    UPDATE_AVAILABLE = "update-available"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class PackStatus:
    pack: Pack
    state: PackState
    cached: CachedPack | None


@dataclass(frozen=True, slots=True)
class Catalogue:
    manifest: Manifest
    source: CatalogueSource
    detail: str | None = None

    def list_packs(
        self, cache: PackCache, *, app_version: str, kind: str | None = None
    ) -> tuple[PackStatus, ...]:
        return tuple(
            pack_status(cache, pack, app_version=app_version)
            for pack in self.manifest.packs
            if kind is None or kind == pack.kind
        )


def pack_status(cache: PackCache, pack: Pack, *, app_version: str) -> PackStatus:
    versions = cache.cached_versions(pack.id)
    exact = next(
        (
            cached
            for cached in versions
            if (cached.version, cached.sha256, cached.size_bytes)
            == (pack.version, pack.sha256, pack.size_bytes)
        ),
        None,
    )
    latest = next(
        (
            cached
            for cached in versions
            if (cached.version, cached.sha256) != (pack.version, pack.sha256)
        ),
        None,
    )
    cached = exact or latest
    if not is_compatible(pack, app_version):
        state = PackState.INCOMPATIBLE
    elif cache.downloading(pack.id):
        state = PackState.DOWNLOADING
    elif exact:
        state = PackState.DOWNLOADED
    elif latest:
        state = PackState.UPDATE_AVAILABLE
    else:
        state = PackState.NOT_DOWNLOADED
    return PackStatus(pack, state, cached)


def resolve_manifest_url(config_url: str = "") -> str:
    url = os.environ.get(MANIFEST_URL_ENV, "").strip() or config_url or DEFAULT_MANIFEST_URL
    return validate_url(url, "Content manifest URL")


@dataclass(frozen=True, slots=True)
class _Saved:
    manifest: Manifest
    headers: dict[str, str]


def _read_saved(path: Path) -> _Saved | None:
    try:
        # The on-disk envelope adds escaping and the two HTTP validators.
        with path.open("rb") as handle:
            body = handle.read(6 * MAX_MANIFEST_BYTES + 8192)
        envelope = json.loads(body)
        values = envelope["manifest"], envelope.get("etag"), envelope.get("last_modified")
        if not isinstance(values[0], str) or any(
            value is not None and (not isinstance(value, str) or "\r" in value or "\n" in value)
            for value in values[1:]
        ):
            return None
        return _Saved(
            parse_manifest(values[0]),
            {
                header: value
                for header, value in zip(
                    ("If-None-Match", "If-Modified-Since"), values[1:], strict=True
                )
                if value
            },
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None


def offline_manifest(cache: PackCache, config_url: str = "") -> Manifest:
    """Read display names for cached downloads without making a network request."""
    try:
        url = resolve_manifest_url(config_url)
    except ManifestError:
        return bundled_manifest()
    key = "manifest-" + hashlib.sha256(url.encode("utf-8")).hexdigest()
    saved = _read_saved(cache.root / key / "catalogue.json")
    return saved.manifest if saved else bundled_manifest()


def _save(path: Path, manifest: str, headers: Mapping[str, str]) -> None:
    envelope = {
        "manifest": manifest,
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
    }
    staged = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as handle:
            staged = Path(handle.name)
            handle.write(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
        staged.replace(path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _fallback(saved: _Saved | None, detail: str) -> Catalogue:
    if saved is not None:
        return Catalogue(saved.manifest, CatalogueSource.CACHED, detail)
    return Catalogue(bundled_manifest(), CatalogueSource.BUNDLED, detail)


async def fetch_catalogue(
    cache: PackCache, *, config_url: str = "", timeout: float = 15
) -> Catalogue:
    """Refresh the catalogue, retaining the last valid response for this URL."""
    url = resolve_manifest_url(config_url)
    key = "manifest-" + hashlib.sha256(url.encode("utf-8")).hexdigest()
    record = cache.root / key / "catalogue.json"
    try:
        with cache._work(key):
            saved = _read_saved(record)
            async with (
                create_http_session() as session,
                session.get(
                    url,
                    headers=saved.headers if saved else {},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response,
            ):
                if response.status == 304 and saved:
                    return Catalogue(saved.manifest, CatalogueSource.LIVE)
                if response.status != 200:
                    return _fallback(
                        saved, f"The catalogue server returned HTTP {response.status}."
                    )
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_MANIFEST_BYTES:
                        raise ManifestError("The manifest exceeds the size limit.")
                raw = body.decode("utf-8-sig")
                manifest = parse_manifest(raw)
                try:
                    _save(record, raw, response.headers)
                except OSError as exc:
                    return Catalogue(
                        manifest,
                        CatalogueSource.LIVE,
                        f"Could not save the offline catalogue: {exc}",
                    )
                return Catalogue(manifest, CatalogueSource.LIVE)
    except (aiohttp.ClientError, OSError, ValueError, TimeoutError) as exc:
        return _fallback(_read_saved(record), f"Could not refresh the catalogue: {exc}")
