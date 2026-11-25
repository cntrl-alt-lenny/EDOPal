import gzip
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from edopal.features.formats.builder.__main__ import build, check_samples
from edopal.features.formats.builder.pool import Index, Printing, build_pool, digest, effective, list_hash
from edopal.features.formats.builder.profiles import profiles

PACK = Path(__file__).resolve().parents[2] / "packs/formats"


def test_release_pack_matches_the_offline_profiles_and_historical_controls():
    if not (PACK / "manifest.json").exists():
        pytest.skip("The separate edopal-packs checkout is required for this pack integration test.")
    manifest = json.loads((PACK / "manifest.json").read_bytes())
    assets = {p.relative_to(PACK).as_posix() for p in PACK.rglob("*") if p.is_file()}
    assert assets == set(manifest["files"]) | {"manifest.json"}
    for rel, sha in manifest["files"].items():
        assert digest((PACK / rel).read_bytes()) == sha
    pinned = profiles()
    assert len(manifest["formats"]) == len(pinned) == 128
    assert sum(r["region"] == "OCG" for r in pinned) == 35
    assert sum(r["status"] == "historical" for r in pinned) == 125
    for row, profile in zip(manifest["formats"], pinned, strict=True):
        assert all(value == row[key] for key, value in profile.items() if key != "validation")
        assert row["validation"] | profile["validation"] == row["validation"]
        v = row["validation"]
        assert v["decks"] == v["passed_decks"] + v["failed_decks"]
        assert v["failures"] == v["failed_slots"]
        if row["status"] == "stock":
            assert "lflist" not in row and row["stock_lflist"].startswith("repositories/lflists/")
            assert row["pool"] > 13000 and row["date"] == "Rolling"
            continue
        if row["status"] == "unsupported":
            assert row["id"] == "genesys" and "points" in row["reason"] and "lflist" not in row
            continue
        raw = (PACK / row["lflist"]).read_bytes()
        lines = raw.decode("utf-8-sig").splitlines()
        assert [s[1:] for s in lines if s.startswith("!")] == [row["display"]]
        assert lines.count("$whitelist") == 1
        entries = [tuple(map(int, line.split()[:2])) for line in lines if line[:1].isdigit()]
        assert all(code > 0 and cap in range(4) for code, cap in entries)
        assert digest(raw) == row["sha256"] == manifest["files"][row["lflist"]]
        assert list_hash(entries) == row["hash"]
        assert len(dict(entries)) == row["printings"]

        if row["id"] in ("kashtira", "overlord"):
            assert dict(entries)[53804307] == 1
        if row["id"] == "ocg-ravine-ruler":
            assert dict(entries)[3280747] == 0
        if row["id"] == "goat":
            assert row["hash"] == "0x6d9ed1c5"
            assert row["display"] == "2005.4 GOAT"
        if row["id"] == "hat":
            assert entries == sorted(entries)
            assert row["hash"] == "0x23f83669"
            assert (row["pool"], row["printings"]) == (6148, 6359)
            assert (v["passed_decks"], v["slots"], v["failed_slots"]) == (4, 280, 0)
            limits = dict(entries)
            assert [limits[i] for i in (55144522, 88264988, 48686504, 511003007, 4779823, 75253697)] == [0, 1, 2, 3, 3, 3]
            assert {17626381, 20409757, 94415058, 21502796, 88264978}.isdisjoint(limits)


def test_rebuild_uses_read_only_db_and_keeps_stock_goat_bytes(tmp_path):
    game = tmp_path / "game"
    stock = game / "repositories/lflists/GOAT.lflist.conf"
    stock.parent.mkdir(parents=True)
    stock.write_bytes(b"!2005.4 GOAT\n$whitelist\n100 1\n100 1\n")
    with closing(sqlite3.connect(game / "cards.cdb")) as con, con:
        con.executescript("""
            CREATE TABLE datas(id INTEGER PRIMARY KEY, type INTEGER, alias INTEGER, ot INTEGER);
            CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO datas VALUES (100, 1, 0, 3), (200, 1, 100, 8);
            INSERT INTO texts VALUES (100, 'Example'), (200, 'Example (Pre-Errata)');
        """)
    (game / "script").mkdir()
    (game / "script/c200.lua").write_text("-- Test historical implementation\n")
    formats = {
        row["name"]: dict(
            id=row["fl_id"],
            category="TCG",
            isPopular=True,
            era="GX",
            date=row["date"],
            banlist=row["banlist"],
            count=1,
            bans={"34": 1},
            samples=[],
            eventName="Example",
        )
        for row in profiles()
        if row["name"] in ("Goat", "HAT")
    }
    sources = dict(cards=[dict(id=34, name="Example", artworkId="100", tcgDate="2002-01-01")], formats=formats, responses={})
    source = tmp_path / "regional.json.gz"
    source.write_bytes(gzip.compress(json.dumps(sources).encode()))
    history = tmp_path / "history.json"
    history.write_text(json.dumps(dict(dates=[f["date"] for f in formats.values()], cards={"100": {"2002-01-01": [200]}})))
    snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in game.rglob("*") if p.is_file()}
    first = build(game, tmp_path / "a", date(2026, 11, 25), source=source, history_file=history)
    second = build(game, tmp_path / "b", date(2026, 11, 25), source=source, history_file=history)
    assert first == second
    assert (tmp_path / "a/lflists/goat.lflist.conf").read_bytes() == stock.read_bytes()
    assert (tmp_path / "a/lflists/hat.lflist.conf").read_text().endswith("200 1\n")
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in game.rglob("*") if p.is_file()} == snapshot
    goat = next(r for r in first["formats"] if r["name"] == "Goat")
    assert goat["hash"] == list_hash([(100, 1), (100, 1)])
    assert goat["hash"] != list_hash([(100, 1)])
    with pytest.raises(ValueError, match="outside the game"):
        build(game, game / "lflists", date.today())


def test_event_exception_and_unavailable_script_and_combined_copy_cap():
    db = {
        100: Printing("Example", 0, 3, 1),
        101: Printing("Example", 100, 3, 1),
        200: Printing("Example (Pre-Errata)", 100, 8, 1),
        300: Printing("Supply Squad", 0, 3, 1),
    }
    cards = [dict(id=10, name="Example", konamiCode="100"), dict(id=30, name="Supply Squad", konamiCode="300")]
    index = Index(cards, db)
    row = dict(name="HAT", host=dict(main=[1, 60], extra=[0, 15], side=[0, 15]))
    pool = build_pool(row, {10, 30}, {10: 1}, index, db, {100: [200]}, {}, set())
    assert pool.entries == {100: 1, 101: 1}
    assert pool.fallback == {"unavailable": 1}
    assert pool.exclusions[0]["name"] == "Supply Squad"
    assert effective({100: 1}, 109, 100) == 1
    assert effective({100: 1}, 110, 100) is None
    sample = dict(id=1, event="TEST", main={"10": 1}, extra={}, side={"10": 1})
    (result,) = check_samples([sample], row, pool, db)
    assert result["slots"] == 2
    assert result["failures"] == [dict(section="side", code=100, name="Example", reason="Copy 2 exceeds limit 1")]
    sample["side"]["10"] = -1
    with pytest.raises(ValueError, match="Invalid card counts"):
        check_samples([sample], row, pool, db)
