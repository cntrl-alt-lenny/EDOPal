from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from decks import (
    DeckIndexer,
    Sections,
    encode_ydke,
    preview,
    preview_repair,
    preview_ydke,
    save_copy,
)
from tournament_decks import DeckError


def test_upstream_wire_layout_and_lossless_round_trip(game: Path) -> None:
    catalog = DeckIndexer(game).scan().catalog
    # Independent fixture: 04 03 02 01 in Main, no Extra, ff ff ff ff in Side.
    assert encode_ydke(Sections((0x01020304,), (), (0xFFFFFFFF,))) == "ydke://BAMCAQ==!!/////w==!"
    for sections in (
        Sections(),
        Sections((), (), (100,)),
        Sections((100, 1, 100, 0xFFFFFFFF), (210, 220, 210), (2, 1)),
    ):
        deck = preview_ydke(encode_ydke(sections), catalog)
        assert deck.sections == sections
        assert encode_ydke(deck.sections) == encode_ydke(sections)
    empty = preview_ydke("ydke://!!!", catalog)
    path = save_copy(game, "Empty", empty)
    result = DeckIndexer(game).scan().search("Empty")[0]
    assert result.path == path and result.preview == empty


@pytest.mark.parametrize(
    "code",
    [
        "ydke://",
        "ydke://!!!junk",
        "ydke://!!!!",
        "ydke://%%%!!!",
        "ydke://YQ==!!!",
        "ydke://AAAAAA==!!!",
        "ydke://é!!!",
        "https://foo",
        "ydke://" + "A" * 100_000 + "!!!",
    ],
)
def test_hostile_sharing_codes_are_only_previews(game: Path, code: str) -> None:
    library = DeckIndexer(game).scan()
    with pytest.raises(DeckError):
        preview_ydke(code, library.catalog)
    assert len(DeckIndexer(game).scan().decks) == len(library.decks)


@pytest.mark.parametrize(
    "name",
    [
        "../other",
        r"..\escape",
        r"C:\escape",
        "/absolute",
        "NUL",
        "LPT1.data",
        "file:stream",
        "x\n#main",
        "x.",
        "..",
        "a" * 300,
    ],
)
def test_invalid_filename_never_publishes(game: Path, name: str) -> None:
    library = DeckIndexer(game).scan()
    with pytest.raises(DeckError):
        save_copy(game, name, preview(Sections((1,)), library.catalog))
    assert len(DeckIndexer(game).scan().decks) == 3


def test_concurrent_saves_and_repair_preserve_originals(game: Path) -> None:
    original = game / "deck/Player.ydk"
    contents = b"# player's note\r\n#main\r\n902\r\n1\r\n902\r\n#extra\r\n900\r\n!side\r\n901\r\n903\r\n904\r\n"
    original.write_bytes(contents)
    library = DeckIndexer(game).scan()
    selected = library.search("Player")[0].preview
    assert selected
    repair = preview_repair(selected, library.catalog)
    assert {c.before: (c.after, c.copies) for c in repair.changes} == {902: (210, 2), 900: (230, 1)}
    assert repair.copy.sections == Sections((210, 1, 210), (230,), (901, 903, 904))
    assert set(repair.unresolved) == {901, 903, 904}
    assert original.read_bytes() == contents and len(list(original.parent.glob("Player*.ydk"))) == 1
    with ThreadPoolExecutor(4) as pool:
        paths = list(pool.map(lambda _: save_copy(game, "Player", repair.copy), range(6)))
    assert len(set(paths)) == 6 and original not in paths
    assert all(path.parent == game / "deck" for path in paths)
    after = DeckIndexer(game).scan()
    assert all(d.preview == repair.copy for d in after.decks if d.path in paths)
    assert original.read_bytes() == contents and not list(original.parent.glob("*.tmp"))
    assert preview_repair(repair.copy, library.catalog).changes == ()
