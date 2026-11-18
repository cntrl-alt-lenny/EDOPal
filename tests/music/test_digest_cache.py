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
    visit(b"cccc")
    database.write_bytes(b"broken")
    visit(b"cccc")
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
