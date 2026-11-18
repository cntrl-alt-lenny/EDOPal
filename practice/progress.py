"""Transactional progress beside the app config, shared across installation locations."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .puzzles import Puzzle

STATES = ("Not started", "Attempted", "Solved")


@dataclass(frozen=True)
class Progress:
    status: str = STATES[0]
    note: str = ""
    played: str = ""


class ProgressChanged(ValueError):
    pass


@contextmanager
def _transaction(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS puzzles (path TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, played TEXT NOT NULL)"
        )
        yield db
        db.commit()
    finally:
        db.close()


def read_progress(path: Path, puzzles: list[Puzzle]) -> dict[str, Progress]:
    if not path.exists():
        return {}
    with _transaction(path) as db:
        rows = {
            key: (digest, Progress(state, note, played))
            for key, digest, state, note, played in db.execute("SELECT * FROM puzzles")
        }
        current = {p.path for p in puzzles}
        copies = Counter(p.digest for p in puzzles)
        previous: dict[str, list[str]] = {}
        for key, (digest, _) in rows.items():
            previous.setdefault(digest, []).append(key)
        result = {}
        for puzzle in puzzles:
            key = puzzle.path
            if key not in rows:
                # Identical files can represent separate exercises. Only follow an
                # unambiguous move when the old path has actually disappeared.
                matches = previous.get(puzzle.digest, [])
                if (
                    not puzzle.digest
                    or copies[puzzle.digest] != 1
                    or len(matches) != 1
                    or matches[0] in current
                ):
                    continue
                key = matches[0]
            digest, progress = rows[key]
            if puzzle.digest and (key != puzzle.path or digest != puzzle.digest):
                db.execute(
                    "UPDATE puzzles SET path=?, digest=? WHERE path=?",
                    (puzzle.path, puzzle.digest, key),
                )
            result[puzzle.path] = progress
        return result


def save_progress(path: Path, puzzle: Puzzle, before: Progress, after: Progress) -> None:
    if after.status not in STATES:
        raise ValueError("Choose a progress state.")
    if after.played and date.fromisoformat(after.played).isoformat() != after.played:
        raise ValueError("Use YYYY-MM-DD for the last-played date.")
    with _transaction(path) as db:
        row = db.execute(
            "SELECT status,note,played FROM puzzles WHERE path=?", (puzzle.path,)
        ).fetchone()
        if (Progress(*row) if row else Progress()) != before:
            raise ProgressChanged(
                "Another window saved progress for this puzzle. Reopen it to see the latest entry."
            )
        db.execute(
            "INSERT OR REPLACE INTO puzzles VALUES(?,?,?,?,?)",
            (puzzle.path, puzzle.digest, after.status, after.note, after.played),
        )


def filter_puzzles(
    puzzles: list[Puzzle], progress: dict[str, Progress], term: str, collection: str, state: str
) -> list[Puzzle]:
    query = term.strip().casefold()
    return [
        p
        for p in puzzles
        if (not collection or p.collection == collection)
        and (not state or progress.get(p.path, Progress()).status == state)
        and query in "\n".join((p.title, p.objective, p.setup)).casefold()
    ]


def next_unsolved(
    rows: list[Puzzle], progress: dict[str, Progress], current: Puzzle | None
) -> Puzzle | None:
    start = rows.index(current) + 1 if current in rows else 0
    return next(
        (
            p
            for p in rows[start:] + rows[:start]
            if progress.get(p.path, Progress()).status != "Solved"
        ),
        None,
    )
