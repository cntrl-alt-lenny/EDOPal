from dataclasses import replace
from pathlib import Path
from threading import Event

from decks import Banlist, Card, Catalog, Finding, Library, SavedDeck, Sections, compare, preview
from studio.pages.mydecks_logic import (
    change_lines,
    changed_date,
    finding_text,
    library_rows,
    list_names,
    saved_sections,
)


def catalog() -> Catalog:
    return Catalog(
        {1: Card(1, "First card", 0, 3, 2, "Spell", "", "", None, ())},
        {},
        set(),
        (),
    )


def test_named_lists_with_identical_headers_stay_distinguishable(tmp_path: Path) -> None:
    lists = tuple(Banlist("Local", tmp_path / f"{i}.conf", 2, False, {}, ()) for i in range(2))
    lib = Library(tmp_path, (), catalog(), lists, ())
    assert list_names(lib) == ["Local · 0.conf · line 2", "Local · 1.conf · line 2"]


def test_unreadable_and_duplicate_names_sort_after_larger_decks(tmp_path: Path) -> None:
    cards = catalog()
    large = preview(Sections((1, 1), (), (1,)), cards)
    small = preview(Sections((), (), (1,)), cards)
    folders = (Path("."), Path("A"), Path("A/nested"))
    decks = tuple(
        SavedDeck(tmp_path / f / "Version.ydk", f, i, p)
        for i, (f, p) in enumerate(zip(folders, (small, None, large), strict=True))
    )
    lib = Library(tmp_path, decks, cards, (), ())
    assert [r.deck for r in library_rows(lib, "", False, None, 2)] == [decks[2], decks[0], decks[1]]
    assert (
        library_rows(lib, "FIRST", True, folders[0], 0)[0].counts
        == "0 Main   ·   0 Extra   ·   1 Side"
    )
    assert changed_date(10**40) == "Unknown date"


def test_moves_and_net_differences_keep_direction() -> None:
    cards = catalog()
    before = preview(Sections((1, 1, 1, 50), (), ()), cards)
    after = preview(Sections((1, 1, 60), (), (1,)), cards)
    lines = change_lines(before, after, compare(before.sections, after.sections))
    assert lines == [
        "First card  ·  1 moved from Main to Side",
        "Unknown card #50  ·  1 removed from Main",
        "Unknown card #60  ·  1 added to Main",
    ]
    assert "outside this list’s allowed cards" in finding_text(
        Finding("outside-whitelist", "First card (1) is absent from the whitelist.", (1,))
    )


def test_saved_sections_group_printings_and_use_local_png(tmp_path: Path) -> None:
    cards = catalog()
    cards.cards[2] = replace(cards.cards[1], id=2)
    pics = tmp_path / "pics"
    pics.mkdir()
    artwork = pics / "2.png"
    artwork.touch()
    deck = preview(Sections((1, 2, 1), (), (2,)), cards)
    main, extra, side = saved_sections(tmp_path, deck, Event())
    assert [(card.name, card.copies, card.artwork) for card in main.cards] == [
        ("First card", 3, artwork)
    ]
    assert main.type_counts == (("Spell", 3),)
    assert (main.count, extra.count, side.count) == (3, 0, 1)
