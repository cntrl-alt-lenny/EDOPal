from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from .bots import Opponents, read_bots
from .puzzles import Puzzle, read_puzzles


@dataclass
class Library:
    opponents: Opponents
    puzzles: list[Puzzle]
    puzzle_warning: str


def scan(game: Path, cancel: Event, report: Callable[[str], None]) -> Library:
    game = game.resolve()
    if not game.is_dir():
        raise ValueError("The selected game folder is unavailable.")
    report("Checking opponents and installed lists…")
    if cancel.is_set():
        raise InterruptedError("Scan cancelled.")
    bots = read_bots(game, cancel)
    report("Reading puzzle objectives and starting positions…")
    puzzles, warning = read_puzzles(game, cancel)
    if cancel.is_set():
        raise InterruptedError("Scan cancelled.")
    return Library(bots, puzzles, warning)
