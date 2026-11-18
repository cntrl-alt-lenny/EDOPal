import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from config import Config
from content.cache import PackCache
from content.catalogue import (
    CatalogueSource,
    PackState,
    fetch_catalogue,
    pack_status,
    resolve_manifest_url,
)
from content.download import download_pack
from content.manifest import DEFAULT_MANIFEST_URL, ManifestError, parse_manifest

from .conftest import FakeHTTP, pack_entry, send


def manifest(*packs: dict, version: int = 1, **fields: object) -> bytes:
    return json.dumps({"schema_version": version, "packs": packs, **fields}).encode()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("id", "../test"),
        ("title", " "),
        ("kind", "future-kind"),
        ("license", ""),
        ("attribution", None),
        ("source_url", "https://user:pwd@example.test/source"),
        ("version", "1.2"),
        ("download_url", "file:///pack.zip"),
        ("min_app_version", "01.2.3"),
        ("size_bytes", True),
        ("sha256", "a" * 63),
        ("previews", []),
        ("withheld", "true"),
    ],
)
def test_bad_pack_field(key: str, value: object) -> None:
    entry = pack_entry("https://example.test", **{key: value})
    with pytest.raises(ManifestError, match=rf"packs\[0\]\.{key}"):
        parse_manifest(manifest(entry))


def test_missing_unknown_and_duplicate_fields() -> None:
    entry = pack_entry("https://example.test")
    with pytest.raises(ManifestError, match="Duplicate pack id"):
        parse_manifest(manifest(entry, entry))
    with pytest.raises(ManifestError, match="Duplicate JSON field"):
        parse_manifest('{"schema_version":1,"packs":[],"packs":[]}')
    with pytest.raises(ManifestError, match="unknown fields"):
        parse_manifest(manifest({**entry, "future_field": True}))
    del entry["license"]
    with pytest.raises(ManifestError, match="missing license"):
        parse_manifest(manifest(entry))


def test_newer_manifest_skips_unknown_kinds_fields_and_withheld_assets() -> None:
    entry = pack_entry("https://example.test")
    entry["previews"][0]["new_field"] = {"x": True}
    data = manifest(
        {**entry, "new_field": 123},
        {"kind": "future-kind", "new_payload": []},
        {**entry, "id": "awaiting-review", "withheld": True},
        version=2,
        optional_future_root=True,
    )
    result = parse_manifest(data)
    assert result.schema_version == 2
    assert [pack.id for pack in result.packs] == ["example"]
    assert result.get("example").previews[0].url == "https://example.test/preview"


def test_three_sources_conditional_refresh_and_last_good(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "packs")
    url = http.url + "/manifest.json"
    route = "/manifest.json"
    stamp = "Wed, 18 Nov 2026 10:00:00 GMT"

    async def run() -> None:
        result = await fetch_catalogue(cache, config_url=url)
        assert result.source == CatalogueSource.BUNDLED
        assert "404" in result.detail

        http.routes[route] = lambda handler: send(
            handler, manifest(pack_entry(http.url)), ETag='"v1"', **{"Last-Modified": stamp}
        )
        result = await fetch_catalogue(cache, config_url=url)
        assert result.source == CatalogueSource.LIVE
        assert result.manifest.get("example")

        http.routes[route] = lambda handler: send(handler, status=304)
        assert (await fetch_catalogue(cache, config_url=url)).source == CatalogueSource.LIVE
        assert http.calls[-1][1]["If-None-Match"] == '"v1"'
        assert http.calls[-1][1]["If-Modified-Since"] == stamp

        http.routes[route] = lambda handler: send(handler, b"invalid", ETag='"bad"')
        result = await fetch_catalogue(cache, config_url=url)
        assert result.source == CatalogueSource.CACHED
        assert result.manifest.get("example")
        assert "Invalid manifest" in result.detail

        http.routes[route] = lambda handler: send(handler, status=503)
        result = await fetch_catalogue(cache, config_url=url)
        assert result.source == CatalogueSource.CACHED
        assert http.calls[-1][1]["If-None-Match"] == '"v1"'
        assert result.manifest.get("example")
        assert (
            await fetch_catalogue(cache, config_url=url + "?fork")
        ).source == CatalogueSource.BUNDLED

        http.routes[route] = lambda handler: None
        result = await fetch_catalogue(cache, config_url=url)
        assert result.source == CatalogueSource.CACHED
        assert "Could not refresh" in result.detail

        cache.clear()
        assert (await fetch_catalogue(cache, config_url=url)).source == CatalogueSource.BUNDLED

    asyncio.run(run())


def test_config_and_environment_override(
    tmp_path: Path, http: FakeHTTP, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"content_manifest_url": http.url + "/config"}))
    cfg = Config(["--config", str(path)])
    assert PackCache.for_config(cfg).root == tmp_path / "packs"
    assert resolve_manifest_url() == DEFAULT_MANIFEST_URL
    assert resolve_manifest_url(cfg.content_manifest_url) == http.url + "/config"
    monkeypatch.setenv("EDOPRO_CONTENT_MANIFEST_URL", http.url + "/env")
    assert resolve_manifest_url(cfg.content_manifest_url) == http.url + "/env"


def test_state_filter_update_and_incompatible(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "packs")
    entry = pack_entry(http.url)
    http.routes["/manifest.json"] = lambda handler: send(
        handler, manifest(entry, pack_entry(http.url, id="other", kind="music"))
    )
    http.routes["/asset"] = lambda handler: send(handler, b"pack data")
    version = "5.5.0"

    async def run() -> None:
        catalogue = await fetch_catalogue(cache, config_url=http.url + "/manifest.json")
        (status,) = catalogue.list_packs(cache, app_version=version, kind="wallpaper")
        pack = status.pack
        assert status.state == PackState.NOT_DOWNLOADED
        assert pack_status(cache, pack, app_version="5.4.0").state == PackState.INCOMPATIBLE
        await download_pack(cache, pack, app_version=version)
        assert pack_status(cache, pack, app_version=version).state == PackState.DOWNLOADED
        update = pack_status(cache, replace(pack, version="1.10.0"), app_version=version)
        assert update.state == PackState.UPDATE_AVAILABLE
        assert update.cached.version == "1.0.0"

    asyncio.run(run())
