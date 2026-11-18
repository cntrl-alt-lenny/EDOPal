import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from threading import Event as CancelEvent

import pytest

from studio.pages import decks_flow as flow
from studio.pages.decks_preview_data import prepare_preview
from tournament_decks import PAGE_SIZE, Deck, DeckError, DeckPreview, Event, FormatLibrary

GOAT = Event("CUP", "Retro Cup", "Goat", "2025-11-21", 64)
DECKS = tuple(
    Deck(i, title, builder, i)
    for i, (title, builder) in enumerate(
        (("Chaos Control", "Lee"), ("Warrior Toolbox", "Pat"), ("Goat Control", "River")), 1
    )
)
CARDS = (("1000", "1000", "1111"), ("1040",), ())


def library(monkeypatch: pytest.MonkeyPatch, download: Callable) -> FormatLibrary:
    client = FormatLibrary()
    monkeypatch.setattr(client, "download", download)
    return client


def test_paging_and_selection_capture(tmp_path: Path) -> None:
    page = flow.EventPage.search("All formats", "  summer  ")
    assert (page.format, page.query, page.previous()) == ("", "summer", None)
    assert page.following((GOAT,) * (PAGE_SIZE - 1)) is None
    following = page.following((GOAT,) * PAGE_SIZE)
    assert following.number == 2 and following.previous() == page
    assert flow.format_choices(("Speed Duel", "Goat"))[-1] == "Speed Duel"

    standings = flow.StandingsSelection()
    standings.open(GOAT)
    standings.populate(DECKS)
    assert standings.selected == DECKS[:1]
    assert standings.can_select_all
    standings.select({3, 1, 999})
    batch = standings.capture(tmp_path, True)
    assert batch == flow.ImportBatch(tmp_path, GOAT, (DECKS[0], DECKS[2]), True)
    standings.open(None)
    assert standings.capture(tmp_path, False) is None
    assert batch.decks == (DECKS[0], DECKS[2])


def test_import_partial_error_and_undo_protects_personal_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow, "_game_running", lambda _: False)
    monkeypatch.setattr(flow, "local_deck_printings", lambda _: {1000: "Normal", 1040: "Extra"})
    existing = flow.publish_deck(tmp_path, GOAT, DECKS[0], CARDS)
    existing.path.write_text("My earlier edits", encoding="utf-8")
    calls = []

    def download(deck: Deck) -> tuple[tuple[str, ...], ...]:
        calls.append(deck)
        if deck == DECKS[1]:
            raise DeckError("This deck is no longer public.")
        return CARDS

    client = library(monkeypatch, download)

    def not_public(_: Deck) -> DeckPreview:
        raise DeckError("No printing details")

    monkeypatch.setattr(client, "preview", not_public)
    batch = flow.ImportBatch(tmp_path, GOAT, DECKS)
    progress = []
    receipt = flow.import_decks(batch, client, CancelEvent(), progress.append)
    assert calls == list(DECKS[:2])
    assert [event.done for event in progress] == [0, 1, 1]
    assert receipt.unresolved == (1111,)
    assert receipt.skipped == 2
    assert receipt.entries[1].reason == "This deck is no longer public."
    assert receipt.entries[2].reason == "Skipped after a save failed."
    assert receipt.created[0].kept == (existing.path,)
    assert receipt.paths[0] != existing.path

    new = receipt.paths[0]
    data = new.read_bytes()
    repeated = flow.import_decks(batch, client, CancelEvent(), lambda _: None)
    assert repeated.paths == receipt.paths
    assert repeated.created == ()
    assert repeated.entries[0].saved.kept == (existing.path,)
    new.write_text("My new edits", encoding="utf-8")
    kept = flow.undo_import(str(tmp_path), receipt, receipt.created)
    assert kept.kept == (new,) and kept.removed == ()
    assert new.read_text(encoding="utf-8") == "My new edits"
    new.write_bytes(data)
    undone = flow.undo_import(str(tmp_path), receipt, receipt.created)
    assert undone.removed == (new,)
    assert existing.path.read_text(encoding="utf-8") == "My earlier edits"


def test_cancel_during_download_keeps_only_completed_decks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow, "_game_running", lambda _: False)
    cancel = CancelEvent()

    def download(deck: Deck) -> tuple[tuple[str, ...], ...]:
        if deck == DECKS[1]:
            cancel.set()
        return CARDS

    batch = flow.ImportBatch(tmp_path, GOAT, DECKS)
    receipt = flow.import_decks(batch, library(monkeypatch, download), cancel, lambda _: None)
    assert receipt.stopped and len(receipt.paths) == 1
    assert [entry.reason for entry in receipt.entries[1:]] == ["Cancelled before saving."] * 2
    assert tuple((tmp_path / "deck").iterdir()) == receipt.paths


def test_import_receipt_counts_verified_printing_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow, "_game_running", lambda _: False)
    monkeypatch.setattr(
        flow, "local_deck_printings", lambda _: {1000: "Normal", 1114: "Local Card", 1040: "Extra"}
    )
    client = library(monkeypatch, lambda _: CARDS)
    preview = DeckPreview(
        ((("Normal", 2), ("Local Card", 1)), (("Extra", 1),), ()),
        (((1000, "Normal"), (1000, "Normal"), (1114, "Local Card")), ((1040, "Extra"),), ()),
        CARDS,
    )
    monkeypatch.setattr(client, "preview", lambda _: preview)
    receipt = flow.import_decks(
        flow.ImportBatch(tmp_path, GOAT, DECKS[:1]), client, CancelEvent(), lambda _: None
    )
    assert receipt.matched == 1
    assert not receipt.unresolved
    assert "\n1114\n" in receipt.paths[0].read_text(encoding="utf-8")


def test_import_corrects_nibiru_token_even_when_every_downloaded_id_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow, "_game_running", lambda _: False)
    with closing(sqlite3.connect(tmp_path / "cards.cdb")) as db, db:
        db.executescript(
            "CREATE TABLE datas(id INTEGER PRIMARY KEY, type INTEGER);"
            "CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT);"
            "INSERT INTO datas VALUES (27204311, 33), (27204312, 16401);"
            "INSERT INTO texts VALUES (27204311, 'Nibiru, the Primal Being'),"
            "(27204312, 'Primal Being Token');"
        )
    cards = (("27204312", "27204312"), (), ("27204312",))
    nibiru = (27204311, "Nibiru, the Primal Being")
    client = library(monkeypatch, lambda _: cards)
    details = DeckPreview((), ((nibiru, nibiru), (), (nibiru,)), cards)
    monkeypatch.setattr(client, "preview", lambda _: details)
    receipt = flow.import_decks(
        flow.ImportBatch(tmp_path, GOAT, DECKS[:1]), client, CancelEvent(), lambda _: None
    )
    assert receipt.matched == 3 and not receipt.unresolved
    assert receipt.paths[0].read_text(encoding="utf-8").splitlines()[1:] == [
        "#main",
        "27204311",
        "27204311",
        "#extra",
        "!side",
        "27204311",
    ]


def test_game_reopens_between_downloads_and_retry_preserves_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = False

    def download(deck: Deck) -> tuple[tuple[str, ...], ...]:
        nonlocal opened
        opened = deck == DECKS[1]
        return CARDS

    monkeypatch.setattr(flow, "_game_running", lambda _: opened)
    batch = flow.ImportBatch(tmp_path, GOAT, DECKS)
    client = library(monkeypatch, download)
    receipt = flow.import_decks(batch, client, CancelEvent(), lambda _: None)
    assert receipt.game_open and receipt.blocked == "EDOPro is open in the selected game folder."
    assert len(receipt.created) == 1
    assert len(list((tmp_path / "deck").iterdir())) == 1
    opened = False
    monkeypatch.setattr(client, "download", lambda _: CARDS)
    final = flow.resume_import(receipt, batch, client, CancelEvent(), lambda _: None)
    assert len(final.created) == 3
    assert final.created[0] == receipt.created[0]
    assert final.skipped == 0


def test_artwork_requires_matching_installation_and_real_decks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow, "_game_running", lambda _: False)
    (tmp_path / "cards.cdb").write_bytes(b"synthetic")
    batch = flow.ImportBatch(tmp_path, GOAT, DECKS[:1])
    receipt = flow.import_decks(
        batch, library(monkeypatch, lambda _: CARDS), CancelEvent(), lambda _: None
    )
    assert flow.artwork_paths(str(tmp_path), receipt) == receipt.paths
    monkeypatch.setattr(flow, "_game_running", lambda _: True)
    with pytest.raises(flow.GameOpen):
        flow.artwork_paths(str(tmp_path), receipt)
    with pytest.raises(DeckError, match="have moved"):
        flow.artwork_paths(str(tmp_path / "other"), receipt)
    receipt.paths[0].unlink()
    with pytest.raises(DeckError, match="have moved"):
        flow.artwork_paths(str(tmp_path), receipt)


def test_preview_finds_local_art_and_preserves_names_without_art(tmp_path: Path) -> None:
    pics = tmp_path / "pics"
    pics.mkdir()
    art = pics / "1000.png"
    art.touch()
    preview = DeckPreview(
        ((("Dark Magician", 2), ("Mirror Force", 1)), (), ()),
        (((1000, "Dark Magician"), (1000, "Dark Magician"), (0, "Mirror Force")), (), ()),
    )
    result = prepare_preview(tmp_path, preview, CancelEvent())
    assert result[0].count == 3
    assert [card.artwork for card in result[0].cards] == [art, None]
    assert result[0].type_counts == (("Unknown type", 3),)
