from dataclasses import replace
from pathlib import Path

import pytest

from decks import DeckIndexer, Library, Sections, banlist_impact, preflight, preview


@pytest.fixture
def library(game: Path) -> Library:
    return DeckIndexer(game).scan()


def test_standard_sizes_and_all_list_caps(library: Library) -> None:
    tcg = library.banlists[0]
    valid = Sections(tuple(range(2, 42)), (210,), (190,))
    assert preflight(preview(valid, library.catalog), tcg, library.catalog).status == "legal"
    sections = Sections((1, 100, 200, 200) + tuple(range(2, 42)), (210,), (101, 200))
    result = preflight(preview(sections, library.catalog), tcg, library.catalog)
    assert result.status == "illegal"
    assert {f.rule for f in result.findings} == {"forbidden", "limited", "semi-limited"}
    assert all(f.ids and f.reason for f in result.findings)
    for main in (40, 60):
        sections = Sections(
            tuple(range(2, 32)) * 2 if main == 60 else valid.main, (210,) * 15, (200,) * 15
        )
        result = preflight(preview(sections, library.catalog), tcg, library.catalog)
        assert "size" not in {f.rule for f in result.findings}
    for main in (39, 61):
        sections = Sections((2,) * main, (210,) * 16, (200,) * 16)
        result = preflight(preview(sections, library.catalog), tcg, library.catalog)
        assert len([f for f in result.findings if f.rule == "size"]) == 3


def test_whitelist_exact_id_and_artwork_alias_boundary(library: Library) -> None:
    tcg, goat = library.banlists
    assert goat.whitelist and not tcg.whitelist
    sections = Sections((*range(2, 41), 101), (), (120,))
    deck = preview(sections, library.catalog)
    result = preflight(deck, goat, library.catalog)
    assert [(f.rule, f.ids) for f in result.findings if not f.uncertain] == [
        ("outside-whitelist", (120,))
    ]
    result = preflight(deck, tcg, library.catalog)
    assert {f.rule for f in result.findings} == {"limited"}
    assert (
        preflight(deck, replace(goat, limits={**goat.limits, 101: 0}), library.catalog).status
        == "illegal"
    )
    boundary = library.catalog.cards[101]
    cat = replace(library.catalog, cards={**library.catalog.cards, 110: replace(boundary, id=110)})
    result = preflight(preview(Sections((*range(2, 41), 110)), cat), goat, cat)
    assert result.findings[0].rule == "outside-whitelist"


def test_global_name_alias_and_section_placement(library: Library) -> None:
    unlisted = replace(library.banlists[0], limits={})
    for final, rule in ((101, "copy-count"), (130, "name-count"), (120, "copy-count")):
        deck = preview(
            Sections((100, 100) + tuple(range(2, 40)), (), (final, final)), library.catalog
        )
        result = preflight(deck, unlisted, library.catalog)
        assert result.status == "illegal" and rule in {f.rule for f in result.findings}
    deck = preview(Sections((*range(2, 41), 220), (190,), (160,)), library.catalog)
    result = preflight(deck, unlisted, library.catalog)
    assert [f.rule for f in result.findings] == ["placement", "placement", "token"]


@pytest.mark.parametrize(
    ("card", "rule"),
    [(140, "alias"), (150, "scope"), (9999, "unresolved"), (230, "alias"), (170, "scope")],
)
def test_uncertain_cards_cannot_get_clean_verdict(library: Library, card: int, rule: str) -> None:
    deck = preview(Sections((*range(2, 41), card)), library.catalog)
    result = preflight(deck, library.banlists[0], library.catalog)
    assert result.status == "could not verify" and rule in {f.rule for f in result.findings}


def test_special_formats_malformed_rules_and_unreadable_decks(game: Path) -> None:
    folder = game / "lflists"
    folder.mkdir()
    (folder / "special.conf").write_text(
        "!Rush Prereleases\n100 -1\n!Speed Duel\n!Partial\n100 nope\n"
    )
    (game / "lflist.conf").write_text("missing header\n")
    (game / "deck/bad.YDK").write_bytes(b"\xff")
    library = DeckIndexer(game).scan()
    rules = {b.name: b for b in library.banlists}
    for name, sections in (
        ("Rush Prereleases", Sections((2,))),
        ("Speed Duel", Sections(tuple(range(1, 21)))),
        ("Partial", Sections(tuple(range(1, 41)))),
    ):
        result = preflight(preview(sections, library.catalog), rules[name], library.catalog)
        assert result.status == "could not verify"
    negative = preflight(
        preview(Sections((100,)), library.catalog), rules["Rush Prereleases"], library.catalog
    )
    assert negative.status == "illegal"
    assert any(f.rule == "forbidden" and "-1" in f.reason for f in negative.findings)
    (bad,) = library.search("bad")
    assert preflight(bad, rules["Partial"], library.catalog).findings[0].rule == "deck-file"
    assert library.issues


def test_impact_excludes_solely_structural_and_uncertain_failures(library: Library) -> None:
    impact = banlist_impact(library, library.banlists[0])
    assert list(impact) == [entry.path for entry in library.decks[:2]]
    for result in impact.values():
        assert result.status == "illegal"
        assert any(f.rule == "limited" for f in result.findings)


def test_exact_print_exceptions_use_card_order(library: Library) -> None:
    banlist = replace(library.banlists[0], limits={100: 1, 101: 3})
    for first, second, status in ((100, 101, "could not verify"), (101, 100, "illegal")):
        cards = Sections(tuple(range(2, 41)) + (first,), (), (second,))
        result = preflight(preview(cards, library.catalog), banlist, library.catalog)
        assert result.status == status
        assert any(f.rule == "alias-limits" for f in result.findings)


def test_conflicting_metadata_and_ritual_room_options(library: Library) -> None:
    catalog = replace(library.catalog, conflicts={2})
    deck = preview(Sections(tuple(range(2, 42)), (180,)), catalog)
    result = preflight(deck, library.banlists[0], catalog)
    assert result.status == "could not verify"
    assert {f.rule for f in result.findings} == {"conflict", "ritual-placement"}
