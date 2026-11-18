import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from music import digest_cache
from music.digest_cache import browsing_digests


def test_rewrite_replace_and_corruption(tmp_path: Path) -> None:
    path = tmp_path / "recording.flac"
    library = tmp_path / "soundtracks"
    database = tmp_path / "music-digests-v1.json"

    def visit(contents: bytes) -> None:
        with browsing_digests(library) as digest:
            assert digest(path) == hashlib.sha256(contents).hexdigest()

    path.write_bytes(b"aaaa")
    visit(b"aaaa")
    original = path.stat()
    path.write_bytes(b"bbbb")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    visit(b"bbbb")
    replacement = path.with_suffix(".new")
    replacement.write_bytes(b"cccc")
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    replacement.replace(path)
    visit(b"cccc")
    rows = json.loads(database.read_bytes())
    next(iter(rows.values()))[1] = "0" * 64
    database.write_text(json.dumps(rows))
    damaged = database.read_bytes()
    visit(b"cccc")
    assert database.with_suffix(".json.corrupt").read_bytes() == damaged
    database.write_bytes(b"broken")
    visit(b"cccc")
    assert database.with_suffix(".json.corrupt").read_bytes() == b"broken"
    with patch.object(digest_cache, "_digest", side_effect=AssertionError("lost repaired cache")):
        visit(b"cccc")


def test_cache_bound_and_per_file_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "soundtracks"
    monkeypatch.setattr(digest_cache, "_LIMIT", 3)
    for start in (0, 4):
        with browsing_digests(library) as digest:
            for i in range(start, start + 4):
                path = tmp_path / f"{i}.ogg"
                path.write_bytes(bytes([i]))
                digest(path)
    assert len(json.loads((tmp_path / "music-digests-v1.json").read_bytes())) == 3
    checkpoint = Mock(side_effect=[None, InterruptedError])
    with browsing_digests(library, checkpoint) as digest:
        digest(path)
        with pytest.raises(InterruptedError):
            digest(path)
    assert checkpoint.call_count == 2


def test_failed_cache_sync_preserves_previous_file(tmp_path: Path) -> None:
    library = tmp_path / "soundtracks"
    recording = tmp_path / "recording.wav"
    recording.write_bytes(b"old")
    with browsing_digests(library) as lookup:
        lookup(recording)
    cache = tmp_path / "music-digests-v1.json"
    previous = cache.read_bytes()
    recording.write_bytes(b"changed")
    with (
        patch("music.catalogue.os.fsync", side_effect=OSError("sync failed")),
        browsing_digests(library) as lookup,
    ):
        assert lookup(recording) == hashlib.sha256(b"changed").hexdigest()
    assert cache.read_bytes() == previous
    assert not cache.with_suffix(".json.corrupt").exists()


def test_cache_repaired_by_another_instance_before_quarantine(tmp_path: Path) -> None:
    cache = tmp_path / "music-digests-v1.json"
    cache.write_bytes(b"broken")
    read = digest_cache._read

    def read_during_repair(path: Path) -> dict | None:
        result = read(path)
        if result is None:
            digest_cache.atomic_json(path, {})
        return result

    with (
        patch.object(digest_cache, "_read", read_during_repair),
        browsing_digests(tmp_path / "soundtracks"),
    ):
        pass
    assert cache.read_bytes() == b"{}"
    assert not cache.with_suffix(".json.corrupt").exists()


def test_two_instances_merge_independent_hashes(tmp_path: Path) -> None:
    library = tmp_path / "soundtracks"
    files = [tmp_path / f"{name}.flac" for name in ("one", "two")]
    for file in files:
        file.write_bytes(file.name.encode())
    script = """
from pathlib import Path
import sys, time
from music.digest_cache import browsing_digests
library, path, other = map(Path, sys.argv[1:])
with browsing_digests(library) as lookup:
    lookup(path)
    path.with_suffix('.ready').touch()
    deadline = time.monotonic() + 5
    while not other.with_suffix('.ready').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('second instance never started')
        time.sleep(0.01)
"""

    def instance(index: int) -> None:
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(library),
                str(files[index]),
                str(files[1 - index]),
            ],
            check=True,
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(instance, (0, 1)))
    with (
        patch.object(digest_cache, "_digest", side_effect=AssertionError("miss")),
        browsing_digests(library) as digest,
    ):
        for path in files:
            assert digest(path) == hashlib.sha256(path.name.encode()).hexdigest()
