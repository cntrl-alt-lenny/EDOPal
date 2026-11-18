import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Event, Timer
from types import SimpleNamespace

import pytest
from conftest import PNG, FakeHTTP, pack_entry, send

from content.cache import CacheBusyError, PackCache
from content.catalogue import PackState, pack_status
from content.download import DownloadError, DownloadFailure, Progress, download_pack, fetch_preview
from content.manifest import Pack, parse_manifest

APP = "5.5.0"
DATA = bytes(range(256)) * 1024
FIRST = 96 * 1024


@pytest.fixture
def pack(http: FakeHTTP) -> Pack:
    data = json.dumps({"schema_version": 1, "packs": [pack_entry(http.url, DATA)]})
    return parse_manifest(data).packs[0]


def partial(cache: PackCache) -> Path:
    (entry,) = [item for item in cache.list_cached() if item.category == "partial"]
    return entry.path


def interrupt(handler: BaseHTTPRequestHandler) -> None:
    send(handler, DATA[:FIRST], **{"Content-Length": str(len(DATA))})


def ranged(handler: BaseHTTPRequestHandler) -> None:
    start = int(handler.headers["Range"].removeprefix("bytes=").removesuffix("-"))
    send(
        handler,
        DATA[start:],
        status=206,
        **{"Content-Range": f"bytes {start}-{len(DATA) - 1}/{len(DATA)}"},
    )


def test_full_download_progress_and_lazy_previews(
    tmp_path: Path, http: FakeHTTP, pack: Pack
) -> None:
    cache = PackCache(tmp_path / "packs")
    http.routes["/asset"] = lambda handler: send(handler, DATA)
    http.routes["/preview"] = lambda handler: send(handler, PNG)
    progress: list[Progress] = []
    path = asyncio.run(download_pack(cache, pack, app_version=APP, progress=progress.append))
    assert path.read_bytes() == DATA
    assert [call[0] for call in http.calls] == ["/asset"]
    assert progress[0] == Progress(0, len(DATA), 0)
    assert progress[-1].bytes_done == len(DATA)
    assert progress[-1].bytes_per_second > 0
    assert all(item.total_bytes == len(DATA) for item in progress)
    assert asyncio.run(download_pack(cache, pack, app_version=APP)) == path

    preview = replace(pack.previews[0], size_bytes=None)
    pack = replace(pack, previews=(preview,))
    image = asyncio.run(fetch_preview(cache, pack, preview))
    assert image.read_bytes() == PNG
    assert asyncio.run(fetch_preview(cache, pack, preview)) == image
    assert [call[0] for call in http.calls] == ["/asset", "/preview"]
    assert {(entry.category, entry.size_bytes) for entry in cache.list_cached()} == {
        ("pack", len(DATA)),
        ("preview", len(PNG)),
    }
    cache.remove_pack(pack.id)
    assert cache.list_cached() == ()


@pytest.mark.parametrize("mode", ["range", "ignores-range", "invalid-range", "range-rejected"])
def test_resume_interrupted_download(tmp_path: Path, http: FakeHTTP, pack: Pack, mode: str) -> None:
    cache = PackCache(tmp_path / "packs")
    http.routes["/asset"] = interrupt
    with pytest.raises(DownloadError) as error:
        asyncio.run(download_pack(cache, pack, app_version=APP))
    assert error.value.reason == DownloadFailure.TRANSIENT
    assert partial(cache).read_bytes() == DATA[:FIRST]
    assert pack_status(cache, pack, app_version=APP).state == PackState.NOT_DOWNLOADED

    def retry(handler: BaseHTTPRequestHandler) -> None:
        if mode == "range":
            ranged(handler)
        elif mode == "invalid-range":
            send(handler, DATA[FIRST:], status=206, **{"Content-Range": "bytes 1-2/3"})
        elif mode == "range-rejected" and "Range" in handler.headers:
            send(handler, status=416)
        else:
            send(handler, DATA)

    http.routes["/asset"] = retry
    events: list[Progress] = []
    if mode == "invalid-range":
        with pytest.raises(DownloadError) as error:
            asyncio.run(download_pack(cache, pack, app_version=APP, progress=events.append))
        assert error.value.reason == DownloadFailure.TRANSIENT
        assert partial(cache).read_bytes() == DATA[:FIRST]
    else:
        path = asyncio.run(download_pack(cache, pack, app_version=APP, progress=events.append))
        assert path.read_bytes() == DATA
        assert events[0].bytes_done == (FIRST if mode == "range" else 0)
        assert not list(path.parent.glob("*.part"))
    assert http.calls[1][1]["Range"] == f"bytes={FIRST}-"
    if mode == "range-rejected":
        assert "Range" not in http.calls[2][1]


def test_cancel_stalled_download_and_resume(tmp_path: Path, http: FakeHTTP, pack: Pack) -> None:
    cache = PackCache(tmp_path / "packs")
    cancel = Event()
    release = Event()

    def stalled(handler: BaseHTTPRequestHandler) -> None:
        interrupt(handler)
        release.wait(5)

    def user_cancel(event: Progress) -> None:
        if event.bytes_done == FIRST:
            timer.start()

    timer = Timer(0.1, cancel.set)
    http.routes["/asset"] = stalled
    try:

        async def run() -> None:
            with pytest.raises(DownloadError) as error:
                await asyncio.wait_for(
                    download_pack(
                        cache, pack, app_version=APP, progress=user_cancel, cancel=cancel
                    ),
                    timeout=2,
                )
            assert error.value.reason == DownloadFailure.CANCELLED

        asyncio.run(run())
    finally:
        release.set()
        timer.cancel()
    assert partial(cache).stat().st_size == FIRST
    http.routes["/asset"] = ranged
    path = asyncio.run(download_pack(cache, pack, app_version=APP))
    assert path.read_bytes() == DATA


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (404, DownloadFailure.NOT_FOUND),
        (410, DownloadFailure.NOT_FOUND),
        (500, DownloadFailure.TRANSIENT),
    ],
)
def test_http_failure_categories(
    tmp_path: Path, http: FakeHTTP, pack: Pack, status: int, reason: DownloadFailure
) -> None:
    cache = PackCache(tmp_path / "packs")
    http.routes["/asset"] = lambda handler: send(handler, status=status)
    with pytest.raises(DownloadError) as error:
        asyncio.run(download_pack(cache, pack, app_version=APP))
    assert error.value.reason == reason
    assert cache.list_cached() == ()


def test_hash_mismatch_leaves_previous_verified_pack(
    tmp_path: Path, http: FakeHTTP, pack: Pack
) -> None:
    cache = PackCache(tmp_path / "packs")
    http.routes["/asset"] = lambda handler: send(handler, DATA)
    old = asyncio.run(download_pack(cache, pack, app_version=APP))
    http.routes["/asset"] = lambda handler: send(handler, DATA[::-1])
    update = replace(pack, version="2.0.0")
    with pytest.raises(DownloadError) as error:
        asyncio.run(download_pack(cache, update, app_version=APP))
    assert error.value.reason == DownloadFailure.INTEGRITY
    assert [(entry.category, entry.path) for entry in cache.list_cached()] == [("pack", old)]
    assert pack_status(cache, update, app_version=APP).state == PackState.UPDATE_AVAILABLE


def test_size_mismatch_rejected_before_writing(tmp_path: Path, http: FakeHTTP, pack: Pack) -> None:
    cache = PackCache(tmp_path / "packs")
    http.routes["/asset"] = lambda handler: send(handler, DATA[:-1])
    with pytest.raises(DownloadError) as error:
        asyncio.run(download_pack(cache, pack, app_version=APP))
    assert error.value.reason == DownloadFailure.INTEGRITY
    assert cache.list_cached() == ()


def test_disk_space_and_app_version_refuse_before_request(
    tmp_path: Path,
    http: FakeHTTP,
    pack: Pack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = PackCache(tmp_path / "packs")
    monkeypatch.setattr(
        "content.download.shutil.disk_usage", lambda folder: SimpleNamespace(free=len(DATA) - 1)
    )
    with pytest.raises(DownloadError) as error:
        asyncio.run(download_pack(cache, pack, app_version=APP))
    assert error.value.reason == DownloadFailure.DISK_SPACE
    with pytest.raises(DownloadError) as error:
        asyncio.run(download_pack(cache, pack, app_version="5.4.0"))
    assert error.value.reason == DownloadFailure.INCOMPATIBLE
    assert not http.calls
    assert cache.list_cached() == ()


def test_concurrent_download_remove_and_clear(tmp_path: Path, http: FakeHTTP, pack: Pack) -> None:
    cache = PackCache(tmp_path / "packs")
    second = PackCache(cache.root)
    http.routes["/asset"] = lambda handler: send(handler, DATA)
    checked = False

    def check_other_instances(event: Progress) -> None:
        nonlocal checked
        if checked or not event.bytes_done:
            return
        checked = True
        assert pack_status(second, pack, app_version=APP).state == PackState.DOWNLOADING
        with pytest.raises(CacheBusyError):
            second.remove_pack(pack.id)
        with pytest.raises(CacheBusyError):
            second.clear()
        assert second.cleanup_partials(max_age_seconds=0) == 0
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from pathlib import Path; from content.cache import PackCache; "
                "PackCache(Path(sys.argv[1])).clear()",
                str(cache.root),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode != 0
        assert "content.cache.CacheBusyError" in result.stderr

    async def run() -> None:
        task = asyncio.create_task(
            download_pack(cache, pack, app_version=APP, progress=check_other_instances)
        )
        await asyncio.sleep(0)
        with pytest.raises(CacheBusyError):
            await download_pack(second, pack, app_version=APP)
        path = await task
        assert path.read_bytes() == DATA

    asyncio.run(run())
    assert checked
    assert [call[0] for call in http.calls] == ["/asset"]


def test_orphan_cleanup_leaves_current_partials(tmp_path: Path, http: FakeHTTP, pack: Pack) -> None:
    cache = PackCache(tmp_path / "packs")
    http.routes["/asset"] = interrupt
    with pytest.raises(DownloadError):
        asyncio.run(download_pack(cache, pack, app_version=APP))
    assert cache.cleanup_partials() == 0
    assert partial(cache).exists()
    assert cache.cleanup_partials(max_age_seconds=0) == 1
    assert cache.list_cached() == ()
