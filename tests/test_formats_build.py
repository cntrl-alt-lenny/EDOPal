"""Regression cases for the dated pool, its pinned inputs and publication evidence."""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest

from decks.banlists import parse_lists
from formats.catalogue import Catalogue, assets, copy_settings, host_steps, validate
from tools.build_format_lists import build
from tools.format_pool import Index, Printing, build_pool, effective, list_hash
from tools.format_recipes import recipe
from tools.format_sources import Responses
from tools.format_validation import check_deck


def card(fid: int, name: str, code: int) -> dict:
    return {
        "id": fid,
        "name": name,
        "konamiCode": f"{code:08}",
        "tcgDate": "2000-01-01",
        "ocgDate": "2000-01-01",
    }


def test_tokens_normal_black_luster_soldier_type_preserving_alias_and_alternate_art() -> None:
    db = {
        5405694: Printing("Black Luster Soldier", 0, 3, 129),
        5405695: Printing("Black Luster Soldier", 5405694, 3, 129),
        10000100: Printing("Black Luster Soldier", 5405694, 3, 17),
        100: Printing("Mecha Phantom Beast Token", 0, 3, 0x4001),
        101: Printing("Another name", 5405694, 3, 129),
    }
    idx = Index([card(1, "Black Luster Soldier", 5405694), card(2, db[100].name, 100)], db)
    assert idx.families == {1: (5405694, {5405694, 5405695})}
    assert idx.excluded == {2: "Token"}
    assert effective({100: 1}, 109, 100) == 1
    assert effective({100: 1}, 110, 100) is None


def test_historical_version_selection_and_out_of_pool_restrictions() -> None:
    db = {
        100: Printing("Old favourite", 0, 3, 1),
        110: Printing("Old favourite (Pre-Errata)", 100, 8, 1),
    }
    index = Index([card(1, "Old favourite", 100), card(2, "Future promo", 200)], db)
    row = dict(name="Tengu")
    args = (row, {1}, {1: 1, 2: 0}, index, db, {100: [110]}, {})
    chosen = build_pool(*args, {110})
    assert chosen.entries == {110: 1}
    assert "Future promo" in chosen.notes[0]
    assert chosen.historical == {1: [110]}
    missing_script = build_pool(*args, set())
    assert missing_script.entries == {100: 1}
    assert missing_script.fallback == {"unavailable": 1}


def test_duplicate_ids_are_emitted_once_but_stock_hash_counts_both() -> None:
    db = {321: Printing("Test", 0, 3, 1)}
    index = Index([card(1, "Test", 321), card(2, "Test", 321)], db)
    pool = build_pool(dict(name="HAT"), {1, 2}, {}, index, db, {}, {}, set())
    assert pool.entries == {321: 3}
    assert list_hash([(321, 3), (321, 3)]) != list_hash([(321, 3)])
    with pytest.raises(ValueError, match="Conflicting limits"):
        build_pool(dict(name="HAT"), {1, 2}, {2: 1}, index, db, {}, {}, set())


def test_literal_banlist_name_not_slug_and_empty_200_refuses_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = Responses(tmp_path)
    calls = []

    def empty(route: str) -> dict:
        calls.append(route)
        return {}

    monkeypatch.setattr(cache, "json", empty)
    with pytest.raises(ValueError, match="Empty restriction"):
        cache.restrictions(dict(name="Goat", banlist="April 2005", category="TCG"))
    assert calls == ["banlists/cards/April%202005?category=TCG"]


def test_sample_corrects_downloaded_token_collision_and_checks_side_copies() -> None:
    db = {
        100: Printing("Limited", 0, 3, 1),
        200: Printing("Nibiru", 0, 3, 1),
        201: Printing("Primal Being Token", 0, 3, 0x4001),
    }
    index = Index([card(1, "Limited", 100), card(2, "Nibiru", 200)], db)
    row = dict(name="HAT", date="2014-07-12", category="TCG")
    pool = build_pool(row, {1, 2}, {1: 1}, index, db, {}, {}, set())
    raw = "#main\n100\n201\n#extra\n!side\n100\n"
    a = dict(id=1, name="Limited", artworkId=100)
    b = dict(id=2, name="Nibiru", artworkId=200)
    deck = dict(id=3, formatName="HAT", ydk=raw, main=[a, b], extra=[], side=[a])
    sample = check_deck("Example", deck, raw.encode(), row, pool, index, db)
    assert sample["slots"] == 3
    assert sample["artwork_changes"] == 1
    assert sample["conversions"] == [["main", 201, 200]]
    assert [(f["name"], f["reason"]) for f in sample["failures"]] == [
        ("Limited", "Copy 2 exceeds limit 1")
    ]


def test_build_deterministic_from_synthetic_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = tmp_path / "game"
    stock = game / "repositories/lflists/GOAT.lflist.conf"
    stock.parent.mkdir(parents=True)
    stock.write_bytes(b"!2005.4 GOAT\n$whitelist\n100 1\n100 1\n")
    with closing(sqlite3.connect(game / "cards.cdb")) as con, con:
        con.executescript(
            "CREATE TABLE datas(id,type,alias,ot); CREATE TABLE texts(id,name); INSERT INTO datas VALUES(100,1,0,3); INSERT INTO texts VALUES(100,'Example');"
        )
    cache = Responses(tmp_path, True)
    row = dict(id=8, name="Goat", date="2005-08-20", category="TCG", era="GX", banlist="April 2005")

    def response(route: str) -> object:
        if route == "formats":
            return [row]
        if route.startswith("events?"):
            return []
        if "eq:false" in route:
            return 0
        if route.startswith("cards/count"):
            return 1
        if route.startswith("cards?"):
            return [card(34, "Example", 100)]
        if route.startswith("banlists/cards/April%202005?"):
            return {"34": "limited"}
        raise AssertionError(route)

    monkeypatch.setattr(cache, "json", response)
    pin = tmp_path / "history.json"
    pin.write_text("{}")
    outputs = []
    published = []
    replace = Path.replace

    def publish(path: Path, target: Path) -> Path:
        assert path.parent == target.parent and path != target
        published.append(target.name)
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", publish)
    for folder in ("first", "second"):
        target = tmp_path / folder
        m = build(cache, game, target, date(2026, 11, 18), pin)
        assert m["goat_parity"]["differences"] == 0
        assert m["formats"][0]["validation"]["decks"] == 0
        outputs.append({p.name: p.read_bytes() for p in target.iterdir()})
    assert outputs[0] == outputs[1]
    assert published == ["lists.zip", "recipes.json", "manifest.json"] * 2


def test_real_bundle_every_digest_parser_recipe_and_parity(tmp_path: Path) -> None:
    c = Catalogue.load()
    manifest = json.loads((c.directory / "manifest.json").read_bytes())
    assert manifest["goat_parity"]["differences"] == 0
    assert manifest["goat_parity"]["checked"] == 25089
    dated = [r for r in c.rows if "file" in r]
    assert (len(c.rows), len(dated)) == (128, 125)
    assert len([r for r in dated if r["notes"]]) == 5
    with ZipFile(c.directory / "lists.zip") as z:
        assert sorted(z.namelist()) == sorted(r["file"] for r in dated)
        for row in dated:
            path = tmp_path / row["file"]
            path.write_bytes(z.read(path.name))
            validate(path, row)
            (installed,) = parse_lists(path)
            bundled = c.bundled_list(row["id"])
            assert replace(bundled, path=path) == installed
            assert row["validation"]["slots"] == sum(
                s["slots"] for s in row["validation"]["samples"]
            )
    assert {r["name"] for r in c.rows if r.get("unsupported")} == {
        "Genesys",
        "Advanced",
        "Traditional",
    }


def test_frozen_catalogue_resolves_from_extracted_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    directory = tmp_path / "assets/formats"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text('{"formats":[{"id":9}]}')
    (directory / "recipes.json").write_text('[{"id":9}]')
    assert assets() == directory
    assert Catalogue.load().rows == [{"id": 9}]


def test_recipe_switches_in_screen_order_and_scopes() -> None:
    c = Catalogue.load()
    (r,) = c.select("TCG", "5D's", "edison")
    instructions = "\n".join(host_steps(r, c.recipes[r["id"]]))
    assert (
        instructions.index("Untick OCG Ignition Priority")
        < instructions.index("Tick 6 substeps")
        < instructions.index("Tick GOAT Ignition Priority")
    )
    assert r["display"] in copy_settings(r, c.recipes[r["id"]])
    assert len(c.select("OCG")) == 35
    assert recipe({"id": 1, "category": "TCG", "name": "Genesys"})["types"] == [
        "No Pendulum",
        "No Link",
    ]
