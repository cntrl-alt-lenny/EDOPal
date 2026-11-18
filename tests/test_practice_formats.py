from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from decks.banlists import Banlist
from decks.cards import Card, Catalog
from decks.model import Sections, preview
from formats.catalogue import Catalogue
from practice.bots import Bot, Distance, Opponents
from practice.matchups import check_format, distance
from practice.puzzles import parse_puzzle


def test_rank_copy_limits_and_missing_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    base = Card(100, "Lantern", 0, 3, 1, "", "", "", None, ())
    cards = {i: replace(base, id=i, name=str(i)) for i in range(100, 120)}
    cards[101] = replace(base, id=101, alias=100)
    cards[111] = replace(base, id=111, alias=100)
    catalog = Catalog(cards, {}, set(), ())
    pool = Banlist(
        "Spring",
        Path("lists"),
        1,
        True,
        {i: 3 for i in range(100, 120) if i not in {101, 111, 119}} | {100: 1},
        (),
    )
    deck = preview(Sections((100, 101, 111, 119), (), (100, 999)), catalog)
    # Nearby artwork inherits the base limit; the distant printing is outside.
    assert distance(deck, pool, catalog) == Distance(2, 2, 1)
    twins = Catalog({**cards, 117: replace(cards[117], name=cards[118].name)}, {}, set(), ())
    by_name = preview(Sections((117, 117), (), (118, 118)), twins)
    assert distance(by_name, pool, twins) == Distance(0, 1, 0)
    long = preview(Sections(tuple(range(102, 116)) * 3), catalog)
    legal = preview(Sections(tuple(i for i in range(102, 118) if i != 111) * 3), catalog)
    bot = Bot("First", "", "1", (), "", long, "", (), "")
    other = replace(bot, name="Second", preview=legal)
    missing = replace(bot, preview=None, name="Unknown")
    catalogue = Catalogue(Path("."), [{"name": "Spring", "id": 5}], {})
    monkeypatch.setattr(Catalogue, "bundled_list", lambda self, fid: pool)
    opponents = Opponents([missing, bot, other], [], "", "", catalog)
    result = check_format(catalogue, 5, opponents, Event())
    assert [b.name for b in result.bots] == ["Second", "First", "Unknown"]
    assert result.summary == "1 opponent fits Spring. Could not verify 1."
    assert result.bots[1].distance == Distance(3, 0, 0)
    assert result.detail(result.bots[2]) == "Deck unavailable"
    opponents.bots = [bot]
    assert (
        "No bundled opponent fits Spring" in check_format(catalogue, 5, opponents, Event()).summary
    )
    cancelled = Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        check_format(catalogue, 5, opponents, cancelled)


def test_position_summary_separates_spoilers_and_rejects_dynamic_setups() -> None:
    script = """Debug.ReloadFieldBegin(0)
Debug.AddCard(10,1,0,LOCATION_HAND,0,POS_FACEDOWN)
Debug.AddCard(11,0,1,LOCATION_MZONE,0,POS_FACEUP)
Debug.AddCard(12,0,0,LOCATION_SZONE,0,POS_FACEDOWN)
Debug.AddMaximumCard(0,21,22,23)
Debug.ReloadFieldEnd()
Debug.ShowHint("Use the lantern to win this turn!")
aux.BeginPuzzle()
"""
    p = parse_puzzle("demo", script)
    assert p.objective == "Win this turn." and "lantern" not in p.setup
    assert p.hints == ("Use the lantern to win this turn!",)
    assert p.setup == "Win this turn · The setup places 1 in your hand and 4 on your field"
    assert not parse_puzzle("conditional", "if ready then\n" + script + "end").setup
    assert not parse_puzzle("conditional", "if ready then\n" + script + "end").objective
    assert not parse_puzzle("variable", script.replace("LOCATION_HAND", "where")).setup
    assert not parse_puzzle("fake", '--aux.BeginPuzzle()\nlocal example="aux.BeginPuzzle()"').setup
    assert not parse_puzzle("negated", 'Debug.ShowHint("You cannot win this turn.")').objective
    assert not parse_puzzle("spoiler", "--[[message\nHint: use the key to win.\n]]").objective
    assert (
        parse_puzzle("goal", "--[[message\nDefeat your opponent!\n]]").objective
        == "Defeat your opponent!"
    )
