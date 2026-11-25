from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from edopal.core.install import Install
from edopal.features.formats.client import DeckError, DeckPreview, parse_preview
from edopal.features.formats.deck import database_paths, encode_ydk, prepare, read_ydk

FIXTURES = Path(__file__).parent / "fixtures"


def cdb(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE datas(id INTEGER PRIMARY KEY, alias INTEGER)")
        db.execute("CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany("INSERT INTO datas VALUES (?,?)", [(code, alias) for code, alias, _ in rows])
        db.executemany("INSERT INTO texts VALUES (?,?)", [(code, name) for code, _, name in rows])
        db.commit()


def test_replay_historical_and_goat_printings(tmp_path):
    cdb(tmp_path / "expansions" / "cards.cdb", [(21502796, 0, "Ryko, Lightsworn Hunter"), (14878871, 0, "Rescue Cat")])
    cdb(
        tmp_path / "repositories" / "delta-bagooska" / "goat-entries.delta.cdb",
        [(511003007, 21502796, "Ryko, Lightsworn Hunter (Pre-Errata)"), (504700179, 14878871, "Rescue Cat (GOAT)")],
    )
    source = b"created by FL\n#main\n21502796\n14878871\n#extra\n!side\n21502796\n"
    checked = prepare(Install(tmp_path), {511003007: 3, 504700179: 3}, source)
    assert not checked.issues
    assert len(checked.changes) == 2 and "2 × Ryko" in checked.changes[0]
    assert read_ydk(checked.content) == (("511003007", "504700179"), (), ("511003007",))


def test_alias_caps_cover_main_extra_side(tmp_path):
    cdb(tmp_path / "cards.cdb", [(100, 0, "Card"), (101, 100, "Card"), (500, 100, "Card (GOAT)")])
    source = b"#main\n100\n#extra\n101\n!side\n500"
    checked = prepare(Install(tmp_path), {500: 2}, source)
    assert not checked.changes and checked.content == source
    assert all("3 copies" in issue for issue in checked.issues)
    assert "Forbidden" in prepare(Install(tmp_path), {100: 0, 500: 2}, b"#main\n101").issues[0]


def test_evidence_required_for_alternate_artwork(tmp_path):
    install = Install(tmp_path)
    cdb(tmp_path / "cards.cdb", [(100, 0, "One"), (200, 0, "Two")])
    source = b"#main\n101\n200\n"
    original = read_ydk(source)
    detail = DeckPreview((), (((200, "Two"), (100, "One")), (), ()), original)
    checked = prepare(install, {100: 3, 200: 3}, source, detail)
    assert not checked.issues
    assert read_ydk(checked.content)[0] == ("100", "200")
    conflict = DeckPreview((), (((200, "Two"), (100, "Unrelated")), (), ()), original)
    assert prepare(install, {100: 3, 200: 3}, source, conflict).issues
    with pytest.raises(DeckError, match="changed"):
        prepare(install, {100: 3}, b"#main\n200\n", detail)


def test_missing_incomplete_and_unambiguous_printings(tmp_path):
    src = b"#main\n100\n"
    install = Install(tmp_path)
    assert "missing" in prepare(install, {200: 3}, src).issues[0]
    cdb(tmp_path / "cards.cdb", [(100, 0, "Card"), (200, 100, "Card (Pre-Errata)"), (201, 100, "Card (GOAT)")])
    result = prepare(install, {200: 1, 201: 1}, src)
    assert "No unique" in result.issues[0] and result.content == src
    bad = tmp_path / "expansions/broken.cdb"
    bad.parent.mkdir()
    bad.write_bytes(b"not a database")
    result = prepare(install, {200: 1}, src)
    assert "unreadable" in result.issues[0] and result.content == src


def test_database_precedence_and_inactive_dbs(tmp_path):
    cdb(tmp_path / "cards.cdb", [(123, 0, "Final")])
    delta = tmp_path / "repositories/delta-bagooska"
    cdb(delta / "prerelease-x.cdb", [(123, 0, "Provisional")])
    cdb(delta / "cards.delta.cdb", [(500, 123, "Final (GOAT)")])
    cdb(tmp_path / "repositories/other/locale.cdb", [(500, 123, "Unloaded")])
    assert [p.name for p in database_paths(tmp_path)] == ["prerelease-x.cdb", "cards.cdb", "cards.delta.cdb"]
    assert read_ydk(prepare(Install(tmp_path), {500: 1}, b"#main\n123").content)[0] == ("500",)


def test_unchanged_source_keeps_original_bytes(tmp_path):
    source = (FIXTURES / "88769.ydk").read_bytes()
    detail = parse_preview(json.loads((FIXTURES / "88769.json").read_bytes()), 88769)
    rows = {code: name for section in detail.printings for code, name in section}
    for section in detail.original:
        for code in section:
            rows.setdefault(int(code), str(code))
    cdb(tmp_path / "cards.cdb", [(code, 0, name) for code, name in rows.items()])
    checked = prepare(Install(tmp_path), dict.fromkeys(rows, 3), source, detail)
    assert not checked.changes and not checked.issues and checked.content == source
    assert detail.original == read_ydk(encode_ydk(detail.original))


@pytest.mark.parametrize("source", [b"#extra\n1", b"#main\n0", b"#main\n1\n#main\n2", b"#main\n4294967296", b"\xff"])
def test_malformed_decks_are_rejected(source):
    with pytest.raises(DeckError):
        read_ydk(source)


def test_recovery_of_unplayable_export_id_requires_unique_artwork_evidence(tmp_path):
    game = Install(tmp_path)
    path = tmp_path / "cards.cdb"
    cdb(path, [(100, 0, "Card"), (101, 0, "Token")])
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("ALTER TABLE datas ADD COLUMN type INTEGER DEFAULT 1")
        db.execute("UPDATE datas SET type=16385 WHERE id=101")
    src = b"#main\n101"
    detail = DeckPreview((), (((100, "Card"),), (), ()), read_ydk(src))
    checked = prepare(game, {100: 3}, src, detail)
    assert read_ydk(checked.content)[0] == ("100",) and not checked.issues
    src = b"#main\n101\n101"
    ambiguous = DeckPreview((), (((100, "Card"), (101, "Token")), (), ()), read_ydk(src))
    assert prepare(game, {100: 3}, src, ambiguous).issues
    mismatched = DeckPreview((), (((100, "Card"), (100, "Other")), (), ()), read_ydk(src))
    assert prepare(game, {100: 3}, src, mismatched).issues
