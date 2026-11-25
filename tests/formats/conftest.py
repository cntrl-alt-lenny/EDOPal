import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from edopal.core.packs import PackInfo
from edopal.dev.preview import make_core
from edopal.features.formats import http_cache
from edopal.features.formats.builder.profiles import profiles
from edopal.features.formats.service import FormatsService

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def formats_service(tmp_path, monkeypatch):
    """The public winner's recorded list with one synthetic local historical printing."""
    game = tmp_path / "test game"
    game.mkdir()
    detail = json.loads((FIXTURES / "88769.json").read_bytes())
    cards = {int(row["artworkId"]): row["name"] for key in ("main", "extra", "side") for row in detail[key]}
    base, historical = 37781520, 511099999
    with closing(sqlite3.connect(game / "cards.cdb")) as db, db:
        db.execute("CREATE TABLE datas(id INTEGER PRIMARY KEY,alias INTEGER,type INTEGER)")
        db.execute("CREATE TABLE texts(id INTEGER PRIMARY KEY,name TEXT)")
        db.executemany("INSERT INTO datas VALUES(?,0,1)", ((code,) for code in cards))
        db.executemany("INSERT INTO texts VALUES(?,?)", cards.items())
        db.execute("INSERT INTO datas VALUES(?,?,1)", (historical, base))
        db.execute("INSERT INTO texts VALUES(?,?)", (historical, cards[base] + " (Pre-Errata)"))
    rows = profiles()
    hat = next(row for row in rows if row["id"] == "hat")
    pool = (set(cards) - {base}) | {historical}
    limits = "\n".join(f"{code} 3" for code in sorted(pool))
    data = f"!{hat['display']}\n$whitelist\n{limits}\n".encode()
    hat["sha256"] = hashlib.sha256(data).hexdigest()
    root = tmp_path / "packs"
    folder = root / "formats"
    file = folder / hat["lflist"]
    file.parent.mkdir(parents=True)
    file.write_bytes(data)
    manifest = dict(
        schema=1,
        id="formats",
        kind="format",
        name="Classic formats",
        version="1",
        formats=rows,
        credits=[],
        files={hat["lflist"]: hat["sha256"]},
    )
    (folder / "manifest.json").write_text(json.dumps(manifest))
    entry = PackInfo("formats", "format", "Classic formats", "1", "formats", 0, "")
    (root / "catalog.json").write_text(json.dumps({"schema": 1, "packs": [asdict(entry)]}))

    def recorded(url, _etag, _limit):
        parts = urlsplit(url)
        if parts.path == "/api/events":
            query = parse_qs(parts.query)
            if query["filter"] != ["format:eq:HAT"] or query["page"] != ["1"]:
                return 200, {}, b"[]"
            fixture = "hat-events.json"
        else:
            fixture = {
                "/api/events/PPAY25": "PPAY25.json",
                "/api/decks/88769": "88769.json",
                "/api/decks/download/88769": "88769.ydk",
                "/images/decks/thumbnails/88769.png": "88769.png",
            }.get(parts.path)
        if not fixture:
            return 404, {}, b""
        return 200, {}, (FIXTURES / fixture).read_bytes()

    monkeypatch.setattr(http_cache, "_get", recorded)
    core = make_core(game, root, tmp_path / "home")
    core.packs.ensure(entry)
    return FormatsService(core)
