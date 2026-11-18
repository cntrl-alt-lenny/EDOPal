from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event

import pytest

from practice.bots import filter_bots, match_file, read_bots, runtime_warning
from practice.library import scan
from practice.progress import (
    Progress,
    ProgressChanged,
    filter_puzzles,
    next_unsolved,
    read_progress,
    save_progress,
)
from practice.puzzles import Puzzle, parse_puzzle, read_puzzles


def test_local_lists_matches_and_ambiguity(tmp_path: Path) -> None:
    game = tmp_path
    decks = game / "WindBot/Decks"
    decks.mkdir(parents=True)
    with closing(sqlite3.connect(game / "cards.cdb")) as db, db:
        db.execute("CREATE TABLE datas (id,alias,ot,type)")
        db.execute("CREATE TABLE texts (id,name)")
        for i in range(1, 21):
            db.execute("INSERT INTO datas VALUES (?,0,3,1)", (i,))
            db.execute("INSERT INTO texts VALUES (?,?)", (i, f"Garden Scout {i}"))
    (game / "lflist.conf").write_text("!Open\n--Ordinary section comment\n!Restricted\n1 1\n")
    (game / "lflists").mkdir()
    (game / "lflists/Faded.conf").write_bytes(b"\xff")
    rows = [
        dict(name="Garden & Grove", deck="Leaf-Knight", difficulty=2, masterRules=[4, 5]),
        dict(name="Lucky", deck="Unknown"),
        dict(name="Lost pages", deck="Bad"),
    ]
    (decks.parent / "bots.json").write_text(json.dumps(rows))
    (decks / "AI_LeafKnight.ydk").write_text(
        "#main\n" + "\n".join(map(str, list(range(1, 21)) * 2))
    )
    (decks / "AI_Bad.ydk").write_bytes(b"\x00")
    lib = read_bots(game, Event())
    bot, missing, bad = lib.bots
    assert [bot.fit(i) for i in range(3)] == ["Fits", "Does not fit", "Cannot tell"]
    assert bot.checks[1].findings[0].ids == (1,)
    assert missing.problem and bad.problem != missing.problem
    assert [missing.fit(0), bad.fit(0)] == ["Cannot tell"] * 2
    assert filter_bots(lib.bots, "sCoUt", 4, "2", "Does not fit", 1) == [bot]
    assert not filter_bots(lib.bots, "scout", 3, "", "", 0)
    paths = [decks / n for n in ("AI_Leaf Knight.ydk", "AI_LeafKnight.ydk")]
    assert match_file("Leaf-Knight", paths) is None
    assert match_file("LeafKnight", paths) == paths[1]
    assert match_file("../Other", paths) is None


def test_puzzle_literals_and_spoilers(tmp_path: Path) -> None:
    script = r"""--[=[message
Objective: Survive one turn.
Difficulty: 3/5
Your Starting LP: 500
Hint: Save the spell.
]=]
Debug.SetPlayerInfo(0,500,0,0)
Duel.SetLP(0,200)
--Debug.SetPlayerInfo(1,999,0,0)
local example = 'Debug.SetPlayerInfo(1,888,0,0)'
Debug.ShowHint("Use the \"lantern\".")
Debug.ShowHint([=[Watch the moon.]=])
Debug.ShowHint("partial " .. calculated)
aux.BeginPuzzle()
Duel.SetLP(0,0)
--[=[Solution:
Play the lantern.
]=]"""
    p = parse_puzzle("Tutorials/Moon_light.lua", script)
    assert (p.title, p.difficulty, p.objective, p.life) == (
        "Moon light",
        "3/5",
        "Survive one turn.",
        ("200", ""),
    )
    assert p.hints == ('Use the "lantern".', "Watch the moon.", "Save the spell.")
    assert p.solution == "Play the lantern."
    secret = parse_puzzle("Secret.lua", "--Solution:\n--Objective: Secret winning move")
    assert secret.solution == "Objective: Secret winning move" and not secret.objective
    assert not filter_puzzles([p], {}, "lantern", "", "")
    assert filter_puzzles([p], {}, "Survive", "Tutorials", "Not started") == [p]
    for sub in ("Examples", "Canon/Tutorials"):
        path = tmp_path / "puzzles" / sub
        path.mkdir(parents=True)
        (path / "First.lua").write_text(script)
    (path / "Torn.lua").write_bytes(b"\xff")
    items, warning = read_puzzles(tmp_path, Event())
    assert [i.collection for i in items] == ["Canon/Tutorials", "Canon/Tutorials", "Examples"]
    assert not warning and items[1].error
    cancel = Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        read_puzzles(tmp_path, cancel)


def test_missing_assets_and_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("practice.bots.sys.platform", "linux")
    monkeypatch.setattr("practice.bots.shutil.which", lambda *a, **kw: None)
    msg = runtime_warning(tmp_path)
    assert all(name in msg for name in ("Mono", "WindBot.exe", "ExecutorBase.dll"))
    lib = scan(tmp_path, Event(), lambda _: None)
    assert not lib.opponents.bots and lib.opponents.warning and lib.puzzle_warning
    (tmp_path / "WindBot").mkdir()
    (tmp_path / "WindBot/bots.json").write_text("broken")
    assert "could not be read" in read_bots(tmp_path, Event()).warning


def test_progress_survives_update_move_and_distinguishes_twins(tmp_path: Path) -> None:
    path = tmp_path / "app/progress.db"
    puzzle = parse_puzzle("A/P.lua", "--Objective: Survive")
    entry = Progress("Solved", "Try the second route", "2026-02-28")
    save_progress(path, puzzle, Progress(), entry)
    updated = parse_puzzle(puzzle.path, "--Objective: Win")
    assert read_progress(path, [updated]) == {puzzle.path: entry}
    moved = replace(updated, path="B/Moved.lua")
    twin = replace(updated, path="B/Other.lua")
    assert not read_progress(path, [moved, twin])
    assert read_progress(path, [moved]) == {moved.path: entry}
    assert next_unsolved([moved, twin], {moved.path: entry}, moved) == twin
    with pytest.raises(ValueError):
        save_progress(path, moved, entry, Progress("Solved", "", "2026-02-31"))
    assert read_progress(path, [moved])[moved.path] == entry


def test_two_writers_keep_independent_records_and_detect_stale_edits(tmp_path: Path) -> None:
    path = tmp_path / "app/progress.db"
    puzzles = [Puzzle(f"{i}.lua", str(i)) for i in range(2)]
    barrier = Barrier(2)

    def save(p: Puzzle) -> None:
        barrier.wait(timeout=5)
        save_progress(path, p, Progress(), Progress("Attempted", p.path))

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(save, puzzles))
    assert len(read_progress(path, puzzles)) == 2
    with pytest.raises(ProgressChanged):
        save_progress(path, puzzles[0], Progress(), Progress("Solved"))
    assert read_progress(path, puzzles)[puzzles[0].path].note == "0.lua"
