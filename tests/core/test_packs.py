from __future__ import annotations

import hashlib
import io
import json
import ssl
import stat
import threading
import zipfile
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPSHandler
from urllib.response import addinfourl

import pytest
from PIL import Image

from edopal.core import pack_client
from edopal.core.pack_client import GitHubPacks
from edopal.core.packs import PackInfo
from edopal.core.paths import Paths

CATALOG = "https://github.com/cntrl-alt-lenny/edopal-packs/releases/download/catalog/catalog.json"
PACK_URL = "https://github.com/cntrl-alt-lenny/edopal-packs/releases/download/packs-2026.11.25/music-test.zip"
THUMB_URL = "https://raw.githubusercontent.com/cntrl-alt-lenny/edopal-packs/main/thumbs/music-test.webp"
SONG = "audio/menu.ogg"
CONTENTS = b"audio fixture"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifest(**changes):
    return {
        "schema": 1,
        "id": "music-test",
        "kind": "music",
        "name": "Test soundtrack",
        "version": "1.0",
        "credits": [],
        "tracks": [{"id": "menu", "title": "Menu", "path": SONG}],
        "files": {SONG: digest(CONTENTS)},
        **changes,
    }


def archive(payload=CONTENTS, *, metadata=None, extra=(), comment=b""):
    output = io.BytesIO()
    if metadata is None:
        metadata = manifest(files={SONG: digest(payload)})
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.comment = comment
        bundle.writestr("manifest.json", json.dumps(metadata))
        bundle.writestr(SONG, payload)
        for name, body in extra:
            bundle.writestr(name, body)
    return output.getvalue()


def pack(data):
    return PackInfo("music-test", "music", "Test soundtrack", "1.0", PACK_URL, len(data), digest(data), THUMB_URL)


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "config", tmp_path / "data", tmp_path / "cache").ensure()


@pytest.fixture
def network(monkeypatch):
    """Serve fresh response streams and reject any request missing from the test's routes."""
    routes = {}
    requests = []

    class ArchiveStream(io.BytesIO):
        def read(self, size=-1):
            assert size > 0, "Pack downloads must request bounded chunks"
            return super().read(size)

    def https_open(handler, request):
        context = handler._context
        assert request.timeout > 0 and isinstance(context, ssl.SSLContext)
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        requests.append(request)
        result = routes[request.full_url]
        if isinstance(result, Exception):
            raise result
        body, headers = result
        code = 302 if "location" in headers else 200
        stream = ArchiveStream(body) if code == 200 and request.full_url == PACK_URL else io.BytesIO(body)
        response = addinfourl(stream, {"Content-Length": str(len(body)), **headers}, request.full_url, code=code)
        response.msg = "Found" if code == 302 else "OK"
        return response

    def http_open(handler, request):
        pytest.fail(f"Attempted a cleartext request to {request.full_url}")

    monkeypatch.setattr(HTTPSHandler, "https_open", https_open)
    monkeypatch.setattr(HTTPHandler, "http_open", http_open)
    return routes, requests


def test_each_pack_redirect_requires_https(paths, network):
    routes, requests = network
    data = archive()
    info = pack(data)
    service = GitHubPacks(paths)
    cdn = "https://cdn.example.test"
    routes[PACK_URL] = b"", {"location": cdn + "/asset"}
    routes[cdn + "/asset"] = b"", {"location": "http://cdn.example.test/final.zip"}
    with pytest.raises(ValueError, match="HTTPS"):
        service.ensure(info)
    assert [req.full_url for req in requests] == [PACK_URL, cdn + "/asset"]
    assert service.cached(info.id) is None

    routes[cdn + "/asset"] = b"", {"location": "/final.zip"}
    routes[cdn + "/final.zip"] = data, {}
    assert (service.ensure(info) / SONG).read_bytes() == CONTENTS


def test_catalog_etag_filter_refresh_and_offline_restart(paths, network):
    routes, requests = network
    first = pack(archive())
    second = replace(first, id="theme-test", name="Test theme", kind="theme")
    catalog = {"schema": 1, "packs": [asdict(first), asdict(second)]}
    routes[CATALOG] = json.dumps(catalog).encode(), {"ETag": '"v1"'}
    assert GitHubPacks(paths).catalog("music") == [first]

    routes[CATALOG] = HTTPError(CATALOG, 304, "Not Modified", {}, None)
    assert GitHubPacks(paths).catalog("theme", refresh=True) == [second]
    headers = {k.lower(): v for k, v in requests[-1].header_items()}
    assert headers["if-none-match"] == '"v1"'

    routes[CATALOG] = json.dumps({"schema": 1, "packs": [asdict(second)]}).encode(), {"ETag": '"v2"'}
    assert GitHubPacks(paths).catalog(refresh=True) == [second]
    routes[CATALOG] = URLError("offline")
    assert GitHubPacks(paths).catalog(refresh=True) == [second]
    assert len(requests) == 4


def test_pack_download_cache_removal_and_progress(paths, network):
    routes, requests = network
    payload = b"sample music " * 180_000
    data = archive(payload)
    info = pack(data)
    routes[PACK_URL] = data, {}
    progress = []
    service = GitHubPacks(paths)
    assert service.cached(info.id) is None
    directory = service.ensure(info, lambda done, total: progress.append((done, total)))
    assert directory == paths.cache / "packs" / info.id
    assert (directory / SONG).read_bytes() == payload
    assert progress[-1] == (len(data), len(data))
    assert any(0 < done < len(data) for done, _ in progress)
    assert all(total == len(data) for _, total in progress)
    assert [done for done, _ in progress] == sorted(done for done, _ in progress)

    routes.clear()
    offline = GitHubPacks(paths)
    assert offline.cached(info.id) == offline.ensure(info) == directory
    assert len(requests) == 1
    offline.remove(info.id)
    assert offline.cached(info.id) is None
    assert not directory.exists()


def test_cancel_during_transfer_leaves_cache_empty(paths, network):
    routes, _ = network
    data = archive(b"sample music " * 180_000)
    info = pack(data)
    routes[PACK_URL] = data, {}
    stop = threading.Event()

    def cancel_on_progress(done, total):
        if 0 < done < total:
            stop.set()

    service = GitHubPacks(paths)
    with pytest.raises(CancelledError):
        service.ensure(info, cancel_on_progress, cancel=stop)
    assert stop.is_set()
    assert service.cached(info.id) is None
    assert not (paths.cache / "packs" / info.id).exists()
    assert (service.ensure(info, cancel=lambda: False) / SONG).read_bytes() == b"sample music " * 180_000


def test_overlapping_ensures_publish_one_download(paths, network):
    routes, requests = network
    data = archive()
    info = pack(data)
    routes[PACK_URL] = data, {}
    first_streaming = threading.Event()
    second_started = threading.Event()
    allow_publish = threading.Event()
    first, second = GitHubPacks(paths), GitHubPacks(paths)

    def pause_first(done, total):
        first_streaming.set()
        assert allow_publish.wait(5)

    def ensure_second():
        second_started.set()
        return second.ensure(info)

    with ThreadPoolExecutor(2) as pool:
        first_job = pool.submit(first.ensure, info, pause_first)
        assert first_streaming.wait(5)
        second_job = pool.submit(ensure_second)
        try:
            assert second_started.wait(5)
            with pytest.raises(TimeoutError):
                second_job.result(timeout=0.05)
        finally:
            allow_publish.set()
        assert second_job.result(timeout=5) == first_job.result(timeout=5)
    assert len(requests) == 1
    assert (second.cached(info.id) / SONG).read_bytes() == CONTENTS


def test_verified_cache_detects_changes_and_replaces_versions(paths, network):
    routes, _ = network
    data = archive()
    info = pack(data)
    routes[PACK_URL] = data, {}
    service = GitHubPacks(paths)
    directory = service.ensure(info)
    (directory / SONG).write_bytes(b"local corruption")
    assert GitHubPacks(paths).cached(info.id) is None
    assert (service.ensure(info) / SONG).read_bytes() == CONTENTS

    updated = b"new music"
    version = "2.0"
    new_data = archive(updated, metadata=manifest(version=version, files={SONG: digest(updated)}))
    new_info = replace(pack(new_data), version=version)
    routes[PACK_URL] = new_data[:-1], {}
    with pytest.raises(ValueError):
        service.ensure(new_info)
    assert (service.cached(info.id) / SONG).read_bytes() == CONTENTS
    routes[PACK_URL] = new_data, {}
    assert (service.ensure(new_info) / SONG).read_bytes() == updated
    assert json.loads((directory / "manifest.json").read_bytes())["version"] == version


@pytest.mark.parametrize("same_contents", [False, True], ids=["new-version", "repacked"])
def test_publish_failure_restores_previous_directory(paths, network, monkeypatch, same_contents):
    routes, _ = network
    data = archive()
    info = pack(data)
    routes[PACK_URL] = data, {}
    service = GitHubPacks(paths)
    directory = service.ensure(info)
    updated = CONTENTS if same_contents else b"new music"
    version = info.version if same_contents else "2"
    data = archive(updated, metadata=manifest(version=version, files={SONG: digest(updated)}), comment=b"reissued")
    new_info = replace(pack(data), version=version)
    assert info.sha256 != new_info.sha256
    routes[PACK_URL] = data, {}
    replace_directory = pack_client.os.replace
    failed = False

    def intermittent_failure(source, target):
        nonlocal failed
        if not failed and Path(target) == directory:
            failed = True
            raise PermissionError("directory busy")
        return replace_directory(source, target)

    monkeypatch.setattr(pack_client, "_exchange", lambda stage, dest: False)
    monkeypatch.setattr(pack_client.os, "replace", intermittent_failure)
    with pytest.raises(PermissionError, match="directory busy"):
        service.ensure(new_info)
    restarted = GitHubPacks(paths)
    routes.clear()
    assert (restarted.cached(info.id) / SONG).read_bytes() == CONTENTS
    assert restarted.ensure(info) == directory
    routes[PACK_URL] = data, {}
    assert (restarted.ensure(new_info) / SONG).read_bytes() == updated


@pytest.mark.parametrize("change", [{"sha256": "0" * 64}, {"size": 5}])
def test_archive_must_match_catalog(paths, network, change):
    routes, _ = network
    data = archive()
    info = replace(pack(data), **change)
    routes[PACK_URL] = data, {}
    service = GitHubPacks(paths)
    with pytest.raises(ValueError):
        service.ensure(info)
    assert service.cached(info.id) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "music-other"},
        {"kind": "theme"},
        {"version": "2.0"},
        {"files": {SONG: "0" * 64}},
        {"files": {}},
        {"files": {SONG: digest(CONTENTS), "audio/missing.ogg": digest(b"missing")}},
    ],
)
def test_rejects_inconsistent_manifest(paths, network, changes):
    routes, _ = network
    data = archive(metadata=manifest(**changes))
    routes[PACK_URL] = data, {}
    service = GitHubPacks(paths)
    with pytest.raises(ValueError):
        service.ensure(pack(data))
    assert service.cached(pack(data).id) is None


@pytest.mark.parametrize("member", ["audio/../alternate.ogg", r"audio\alternate.ogg", "symlink", "reparse"])
def test_extracts_only_portable_regular_paths(paths, network, member):
    routes, _ = network
    entry = zipfile.ZipInfo(member)
    if member == "symlink":
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    elif member == "reparse":
        entry.create_system = 0
        entry.external_attr = stat.FILE_ATTRIBUTE_REPARSE_POINT
    body = b"fixture"
    data = archive(metadata=manifest(files={SONG: digest(CONTENTS), member: digest(body)}), extra=[(entry, body)])
    routes[PACK_URL] = data, {}
    service = GitHubPacks(paths)
    with pytest.raises(ValueError):
        service.ensure(pack(data))
    assert not (paths.cache / "packs" / "music-test").exists()


@pytest.mark.parametrize(
    ("limit", "bound"),
    [
        ("MAX_ARCHIVE_BYTES", 16),
        ("MAX_FILE_BYTES", 12),
        ("MAX_TOTAL_BYTES", len(json.dumps(manifest()).encode()) + len(CONTENTS) - 1),
        ("MAX_MEMBERS", 1),
    ],
)
def test_enforces_archive_and_extraction_bounds(paths, network, monkeypatch, limit, bound):
    routes, _ = network
    monkeypatch.setattr(pack_client, limit, bound)
    data = archive()
    routes[PACK_URL] = data, {}
    service = GitHubPacks(paths)
    with pytest.raises(ValueError):
        service.ensure(pack(data))
    assert service.cached("music-test") is None


@pytest.mark.parametrize("source_type", ["zip", "folder"])
def test_local_checkout_override(paths, network, tmp_path, source_type):
    _, requests = network
    source = tmp_path / "checkout"
    source.mkdir()
    data = archive()
    if source_type == "zip":
        info = replace(pack(data), url="music-test.zip")
        (source / info.url).write_bytes(data)
    else:
        info = replace(pack(data), url="music/test", size=0, sha256="")
        folder = source / info.url
        (folder / SONG).parent.mkdir(parents=True)
        (folder / SONG).write_bytes(CONTENTS)
        (folder / "manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")
    (source / "catalog.json").write_text(json.dumps({"schema": 1, "packs": [asdict(info)]}), encoding="utf-8")
    service = GitHubPacks(paths, local_dir=source)
    assert service.catalog("music") == [info]
    folder = service.ensure(info)
    assert folder == paths.cache / "packs" / info.id
    assert (folder / SONG).read_bytes() == CONTENTS
    assert not requests


def test_local_folder_checks_manifest_before_publish(paths, network, tmp_path):
    _, requests = network
    source = tmp_path / "checkout"
    folder = source / "music" / "test"
    (folder / SONG).parent.mkdir(parents=True)
    (folder / SONG).write_bytes(b"unfinished local edit")
    (folder / "manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")
    info = replace(pack(archive()), url="music/test", size=0, sha256="", thumbnail="")
    service = GitHubPacks(paths, local_dir=source)
    with pytest.raises(ValueError):
        service.ensure(info)
    assert service.cached(info.id) is None
    assert not requests


def test_thumbnail_cached_across_instances(paths, network):
    routes, requests = network
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "#ebc987").save(data, format="WEBP")
    routes[THUMB_URL] = data.getvalue(), {}
    info = pack(archive())
    thumb = GitHubPacks(paths).thumbnail(info)
    assert paths.cache in thumb.parents and thumb.read_bytes() == data.getvalue()
    routes.clear()
    assert GitHubPacks(paths).thumbnail(info) == thumb
    assert GitHubPacks(paths).thumbnail(replace(info, thumbnail="")) is None
    assert len(requests) == 1
