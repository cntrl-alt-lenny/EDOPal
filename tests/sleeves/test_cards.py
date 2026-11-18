import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path

import pytest

import main
from sleeves import ArtMissing, ArtUnavailable, Card, CardArt, CardIndex, Layout, Library


def test_local_index_reads_all_databases_once(tmp_path: Path) -> None:
    for name, start in [
        ("cards.cdb", 0),
        ("expansions/local.cdb", 10000),
        ("repositories/pack/latest.delta.cdb", 20000),
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript(
                "CREATE TABLE datas (id INTEGER PRIMARY KEY); CREATE TABLE texts(id INTEGER PRIMARY KEY,name TEXT);"
            )
            count = min(10000, 25000 - start)
            db.executemany(
                "INSERT INTO datas VALUES (?)", ((i,) for i in range(start, start + count))
            )
            db.executemany(
                "INSERT INTO texts VALUES (?,?)",
                ((i, "  " if i == 9 else f"Dragon {i % 10}") for i in range(start, start + count)),
            )
            db.commit()
    index = CardIndex(tmp_path)
    for db_path in tmp_path.rglob("*.cdb"):
        db_path.unlink()
    assert index.search("000123", 1) == [Card(123, "Dragon 3")]
    assert len(index.search("AGON 3", 100)) == 100
    assert index.search("nothing") == []
    assert index.search("Dragon", 0) == []
    assert len(index.search("", 25000)) == 24998  # zero ID and blank name omitted


class FakeProvider:
    def __init__(self, replies: list[tuple[int, bytes] | TimeoutError]) -> None:
        self.replies = iter(replies)
        self.urls: list[str] = []
        self.active = 0
        self.peak = 0

    async def __aenter__(self) -> "FakeProvider":
        self.active += 1
        self.peak = max(self.peak, self.active)
        return self

    async def __aexit__(self, *args: object) -> None:
        self.active -= 1

    def get(self, url: str, **kwargs: object) -> "Response":
        self.urls.append(url)
        reply = next(self.replies)
        if isinstance(reply, TimeoutError):
            raise reply
        return Response(*reply)


class Response:
    def __init__(self, status: int, data: bytes) -> None:
        self.status, self.data = status, data
        self.content = self
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> "Response":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        await asyncio.sleep(0)
        yield self.data


def test_fetch_serializes_validates_and_reuses_disk(
    library: Library, blocks: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider([(200, blocks)] * 3)
    monkeypatch.setattr(main, "_create_http_session", lambda **kwargs: provider)

    async def selection() -> list[Path]:
        return await asyncio.gather(*(CardArt(library.root).fetch(i) for i in (71, 82, 71)))

    paths = asyncio.run(selection())
    assert paths[0] == paths[2] and paths[0].read_bytes() == blocks
    assert provider.peak == 1 and len(provider.urls) == 2
    assert all("/cards_cropped/" in url for url in provider.urls)
    entry = library.add_image("Blue dragon", paths[0], Layout("framed"), passcode=71)
    assert library.entries("71")[0] == entry
    paths[0].write_bytes(b"bad cache")
    assert asyncio.run(CardArt(library.root).fetch(71)).read_bytes() == blocks
    assert len(provider.urls) == 3


def test_fetch_with_an_aliased_library_parent(
    tmp_path: Path, blocks: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "temp alias"
    parent.mkdir()
    root = parent / ".." / "library"
    provider = FakeProvider([(200, blocks)])
    monkeypatch.setattr(main, "_create_http_session", lambda **kwargs: provider)
    assert asyncio.run(CardArt(root).fetch(17)).read_bytes() == blocks
    assert (tmp_path / "library/card-art/17.jpg").read_bytes() == blocks


def test_404_persists_until_ttl(
    library: Library, blocks: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider([(404, b""), (200, blocks)])
    monkeypatch.setattr(main, "_create_http_session", lambda **kwargs: provider)
    for _ in range(2):
        with pytest.raises(ArtMissing):
            asyncio.run(CardArt(library.root).fetch(42))
    assert len(provider.urls) == 1
    cache = library.root / "card-art" / "missing.json"
    cache.write_text(json.dumps({"42": time.time() - 15 * 86400}))
    assert asyncio.run(CardArt(library.root).fetch(42)).exists()
    assert len(provider.urls) == 2


@pytest.mark.parametrize("reply", [(429, b""), (503, b""), (200, b"not an image"), TimeoutError()])
def test_transient_failure_is_retryable(
    library: Library,
    blocks: bytes,
    reply: tuple[int, bytes] | TimeoutError,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider([reply, reply, (200, blocks)])
    monkeypatch.setattr(main, "_create_http_session", lambda **kwargs: provider)

    async def no_wait(seconds: float) -> None:
        pass

    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    with pytest.raises(ArtUnavailable):
        asyncio.run(CardArt(library.root).fetch(42))
    assert not (library.root / "card-art" / "missing.json").exists()
    assert asyncio.run(CardArt(library.root).fetch(42)).exists()
