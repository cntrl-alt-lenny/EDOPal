import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from decks import Counts, DeckIndexer, Sections, compare, version_groups
from decks.compare import Change
from decks.library import SavedDeck


def test_metadata_lookup_search_and_worker(game: Path) -> None:
    index = DeckIndexer(game)
    delivered = []

    def receive(deck: SavedDeck) -> None:
        delivered.append((deck, threading.get_ident()))

    with ThreadPoolExecutor(1) as pool:
        library = index.scan_async(pool, receive).result(timeout=10)
    assert [entry for entry, _ in delivered] == list(library.decks)
    assert all(thread != threading.get_ident() for _, thread in delivered)
    assert not library.issues
    assert set(library.catalog.cards) >= {1, 101, 210, 260}
    assert 270 not in library.catalog.cards
    (first,) = library.search("TRIAL V1", "fam")
    assert first.folder == Path("Family")
    assert first.modified_ns == first.path.stat().st_mtime_ns
    result = first.preview
    assert result
    assert result.counts == (4, 1, 2)
    assert result.breakdown == {"monster": 4, "spell": 1, "trap": 1, "other": 0, "unresolved": 1}
    assert result.unresolved == (902,)
    card = result.cards[100]
    assert (card.type_line, card.attribute, card.rating_kind, card.rating, card.archetypes) == (
        "Spellcaster / Monster / Effect",
        "LIGHT",
        "Level",
        4,
        ("Alpha", "Beta"),
    )
    assert result.cards[210].rating_kind == "Rank"
    assert library.catalog.cards[220].rating_kind == "Link"
    assert [m.counts for m in library.by_card("WITch")] == [Counts(1, 0, 2)] * 2
    assert [m.counts for m in library.by_card(100)] == [Counts(1, 0, 1)] * 2
    assert library.by_card("902")[0].deck == first
    assert not library.by_card("") and not library.by_card("12345678901")
    assert version_groups(library.decks) == ((first, library.decks[1]),)


def test_incremental_cache_edit_replace_delete_and_database_change(game: Path) -> None:
    index = DeckIndexer(game)
    a = index.scan()
    b = index.scan()
    assert a.catalog is b.catalog and a.banlists is b.banlists
    assert all(x is y for x, y in zip(a.decks, b.decks, strict=True))
    original = a.decks[0].path
    copy = original.with_suffix(".replacement")
    copy.write_bytes(original.read_bytes().replace(b"902", b"901"))
    # Same byte length and mtime, new file identity.
    os.utime(copy, ns=(original.stat().st_atime_ns, original.stat().st_mtime_ns))
    copy.replace(original)
    a.decks[-1].path.unlink()
    c = index.scan()
    assert len(c.decks) == 2 and c.decks[1] is b.decks[1]
    assert c.by_card(901) and not c.by_card(902)
    with closing(sqlite3.connect(game / "expansions/a.cdb")) as db:
        db.execute("UPDATE texts SET name='Pumpkin' WHERE id=100")
        db.commit()
    d = index.scan()
    assert d.by_card("Pumpkin") and not a.by_card("Pumpkin")
    assert a.by_card("Moon")[0].counts == (1, 0, 2)
    assert d.by_card("Moon")[0].counts == (0, 0, 1)


def test_malformed_files_and_config_traversal(game: Path) -> None:
    bad = game / "deck/bad"
    bad.mkdir()
    for name, contents in {
        "large.YDK": b"#main\n1\n" + b" " * 33000,
        "junk.ydk": b"#comment\0\n#main\n1",
        "bad-utf8.ydk": b"\xff",
        "overflow.ydk": b"#main\n4294967296",
        "bad-order.ydk": b"#extra\n#main\n1",
        "incomplete.ydk": b"#only comments",
        "negative.ydk": b"#main\n-3",
    }.items():
        (bad / name).write_bytes(contents)
    (bad / "ignored.ydk.txt").write_bytes(b"")
    (game.parent / "external").mkdir()
    (game.parent / "external/list.conf").write_text("!Outside")
    (game / "config/user_configs.json").write_text(
        json.dumps({"repos": [{"repo_path": "../external", "lflist_path": "."}]})
    )
    result = DeckIndexer(game).scan()
    assert len(result.decks) == 10
    assert all(d.error and d.preview is None for d in result.search(folder="bad"))
    assert [b.name for b in result.banlists] == ["TCG", "2005.4 GOAT"]
    assert any("outside" in error for error in result.issues)


def test_section_diff_pairs_moves_before_residual_changes() -> None:
    before = Sections((1, 1, 1, 2, 3, 5), (6,), (7, 7))
    after = Sections((1, 1, 2, 2), (1, 5), (7, 8))
    assert set(compare(before, after)) == {
        Change("moved", 1, 1, "main", "extra"),
        Change("moved", 5, 1, "main", "extra"),
        Change("count changed", 2, 1, None, "main"),
        Change("count changed", 7, 1, "side", None),
        Change("removed", 3, 1, "main", None),
        Change("removed", 6, 1, "extra", None),
        Change("added", 8, 1, None, "side"),
    }
    assert not compare(Sections((1, 2)), Sections((2, 1)))


def test_report_reads_only_and_checks_all_pairs(game: Path) -> None:
    def snapshot() -> dict:
        return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in game.rglob("*") if p.is_file()}

    before = snapshot()
    out = subprocess.run(
        [sys.executable, "-B", "-m", "decks", "--report", str(game)],
        capture_output=True,
        encoding="utf-8",
        check=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert "3 decks, 0 unreadable, 1 unresolved IDs" in out.stdout
    assert "Version groups: 1" in out.stdout and "Preflights: 6 " in out.stdout
    assert "outside-whitelist" in out.stdout
    assert snapshot() == before


def test_bad_database_does_not_hide_other_decks(game: Path) -> None:
    (game / "expansions/corrupt.cdb").write_bytes(b"Not a database")
    result = DeckIndexer(game).scan()
    assert result.by_card(100)
    assert any("corrupt.cdb" in message for message in result.issues)
    with pytest.raises(ValueError, match="unavailable"):
        DeckIndexer(game / "nowhere").scan()


def test_configured_list_changes_and_disabled_override(game: Path) -> None:
    index = DeckIndexer(game)
    first = index.scan()
    path = game / "repositories/Local/rules/season.conf"
    path.write_text("!Local change\n1 2\n")
    second = index.scan()
    assert second.decks[0] is first.decks[0]
    assert [(b.name, b.limits) for b in second.banlists] == [("Local change", {1: 2})]
    (game / "config/user_configs.json").write_text(
        json.dumps({"repos": [{"repo_path": "repositories/Local", "should_read": False}]})
    )
    third = index.scan()
    assert not third.banlists and 210 not in third.catalog.cards
    assert first.decks[0].preview and 210 in first.decks[0].preview.cards


def test_pending_database_journal_is_preserved_and_retried(game: Path) -> None:
    index = DeckIndexer(game)
    assert 1 in index.scan().catalog.cards
    journal = game / "cards.cdb-journal"
    journal.write_bytes(b"pending")
    partial = index.scan()
    assert 1 not in partial.catalog.cards and journal.read_bytes() == b"pending"
    assert any("pending journal" in issue for issue in partial.issues)
    journal.unlink()
    assert 1 in index.scan().catalog.cards


def test_configured_database_order_includes_deltas_once(game: Path) -> None:
    data = game / "repositories/Local/data"
    path = data / "z.delta.cdb"
    shutil.copyfile(data / "non-delta.cdb", path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE datas SET level=7 WHERE id=210")
        db.commit()
    result = DeckIndexer(game).scan()
    assert result.catalog.cards[210].rating == 7


def test_linked_deck_tree_stays_in_selected_install(game: Path) -> None:
    outside = game.parent / "outside"
    outside.mkdir()
    (outside / "private.ydk").write_text("#main\n1\n")
    link = game / "deck/linked"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        result = DeckIndexer(game).scan()
        assert not result.search("private")
        assert any("linked deck directory" in issue for issue in result.issues)
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
