"""Transactional local history and card tags for the decks observed by EDOPal."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from .library import Library, _check_cancelled
from .model import SavedDeck, Sections

VERSIONS_PER_DECK = 50
VERSIONS_TOTAL = 10_000
RETENTION_DAYS = 365
GROUP_LIMIT = 24
GROUP_NAME_LIMIT = 60
DAY_NS = 86_400_000_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    game TEXT PRIMARY KEY, started INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL,
    path TEXT NOT NULL,
    file_key TEXT,
    sections TEXT NOT NULL,
    modified INTEGER NOT NULL,
    missing INTEGER,
    UNIQUE(game, path)
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    track INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    sections TEXT NOT NULL,
    modified INTEGER NOT NULL,
    captured INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS versions_track ON versions(track, id DESC);
CREATE INDEX IF NOT EXISTS versions_age ON versions(captured, id);
CREATE TABLE IF NOT EXISTS tag_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    UNIQUE(track, name_key)
);
CREATE TABLE IF NOT EXISTS tags (
    tag_group INTEGER NOT NULL REFERENCES tag_groups(id) ON DELETE CASCADE,
    code INTEGER NOT NULL,
    PRIMARY KEY(tag_group, code)
);
"""


@dataclass(frozen=True)
class Track:
    id: int
    path: Path


@dataclass(frozen=True)
class Ledger:
    active: dict[Path, Track]
    missing: tuple[Track, ...]


@dataclass(frozen=True)
class Version:
    sections: Sections
    modified_ns: int


@dataclass(frozen=True)
class Timeline:
    latest: Version
    previous: tuple[Version, ...]
    missing: bool


@dataclass(frozen=True)
class TagGroup:
    id: int
    name: str
    cards: frozenset[int] = frozenset()


def _version(row: sqlite3.Row) -> Version:
    return Version(
        Sections(*(tuple(cards) for cards in json.loads(row["sections"]))), row["modified"]
    )


def _file_key(deck: SavedDeck) -> str | None:
    return ":".join(map(str, deck.file_id)) if deck.file_id else None


class DeckStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(
        self, *, write: bool = False, create: bool = False
    ) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=8)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.row_factory = sqlite3.Row
            if create:
                db.executescript(SCHEMA)
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    def observe(self, library: Library, started: int, cancel: Event | None = None) -> Ledger:
        """Commit one complete observation in order, with no writes to the installation."""
        _check_cancelled(cancel)
        game = library.game
        folder = game / "deck"
        if self.path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("Choose a settings folder outside the game's deck folder.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(write=True, create=True) as db:
            last = db.execute("SELECT started FROM scans WHERE game=?", (str(game),)).fetchone()
            if not last or last[0] < started:
                self._record(db, library, started, cancel)
                db.execute(
                    "INSERT INTO scans VALUES (?, ?) ON CONFLICT(game) DO UPDATE SET started=excluded.started",
                    (str(game), started),
                )
                self._prune(db, started)
            _check_cancelled(cancel)
            return self._ledger(db, game)

    @staticmethod
    def _ledger(db: sqlite3.Connection, game: Path) -> Ledger:
        active, missing = {}, []
        for row in db.execute(
            "SELECT id, path, missing FROM tracks WHERE game=? ORDER BY missing DESC, path",
            (str(game),),
        ):
            track = Track(row["id"], game / "deck" / row["path"])
            if row["missing"] is None:
                active[track.path] = track
            else:
                missing.append(track)
        return Ledger(active, tuple(missing))

    @staticmethod
    def _record(
        db: sqlite3.Connection, library: Library, started: int, cancel: Event | None
    ) -> None:
        game = str(library.game)
        folder = library.game / "deck"
        old = {r["path"]: r for r in db.execute("SELECT * FROM tracks WHERE game=?", (game,))}
        seen = {deck.path.relative_to(folder).as_posix(): deck for deck in library.decks}
        gone = old.keys() - seen.keys()
        old_keys = Counter(row["file_key"] for row in old.values())
        new_keys = Counter(map(_file_key, seen.values()))
        moves = (
            {
                r["file_key"]: r
                for path in gone
                if (r := old[path])["file_key"] and old_keys[r["file_key"]] == 1
            }
            if library.deck_scan_complete
            else {}
        )
        for path, deck in seen.items():
            _check_cancelled(cancel)
            row = old.get(path)
            if deck.preview is None:
                if row and row["missing"] is not None:
                    db.execute("UPDATE tracks SET missing=NULL WHERE id=?", (row["id"],))
                continue
            key = _file_key(deck)
            content = json.dumps(deck.preview.sections, separators=(",", ":"))
            if (
                row is None
                and key
                and new_keys[key] == 1
                and (candidate := moves.get(key))
                and candidate["sections"] == content
            ):
                row = candidate
                gone.remove(row["path"])
            if row is None:
                db.execute(
                    "INSERT INTO tracks(game, path, file_key, sections, modified) VALUES (?, ?, ?, ?, ?)",
                    (game, path, key, content, deck.modified_ns),
                )
                continue
            if row["sections"] != content:
                db.execute(
                    "INSERT INTO versions(track, sections, modified, captured) VALUES (?, ?, ?, ?)",
                    (row["id"], row["sections"], row["modified"], started),
                )
                db.execute(
                    """DELETE FROM versions WHERE track=? AND id IN (
                       SELECT id FROM versions WHERE track=? ORDER BY id DESC LIMIT -1 OFFSET ?)""",
                    (row["id"], row["id"], VERSIONS_PER_DECK),
                )
            if (row["path"], row["file_key"], row["sections"], row["modified"], row["missing"]) != (
                path,
                key,
                content,
                deck.modified_ns,
                None,
            ):
                db.execute(
                    "UPDATE tracks SET path=?, file_key=?, sections=?, modified=?, missing=NULL WHERE id=?",
                    (path, key, content, deck.modified_ns, row["id"]),
                )
        if library.deck_scan_complete:
            db.executemany(
                "UPDATE tracks SET missing=? WHERE id=? AND missing IS NULL",
                ((started, old[path]["id"]) for path in gone),
            )

    @staticmethod
    def _prune(db: sqlite3.Connection, now: int) -> None:
        cutoff = now - RETENTION_DAYS * DAY_NS
        db.execute("DELETE FROM tracks WHERE missing < ?", (cutoff,))
        db.execute("DELETE FROM versions WHERE captured < ?", (cutoff,))
        db.execute(
            """DELETE FROM versions WHERE id IN (
               SELECT id FROM versions ORDER BY captured DESC, id DESC LIMIT -1 OFFSET ?)""",
            (VERSIONS_TOTAL,),
        )

    def timeline(self, track: Track) -> Timeline:
        with self._connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track.id,)).fetchone()
            if row is None:
                raise ValueError("This deck's saved history has expired. Refresh the library.")
            rows = db.execute(
                "SELECT sections, modified FROM versions WHERE track=? ORDER BY id DESC",
                (track.id,),
            )
            path = Path(row["game"]) / "deck" / row["path"]
            return Timeline(
                _version(row),
                tuple(map(_version, rows)),
                not path.is_file(),
            )

    def groups(self, track: Track) -> tuple[TagGroup, ...]:
        with self._connect() as db:
            return self._groups(db, track.id)

    @staticmethod
    def _groups(db: sqlite3.Connection, track: int) -> tuple[TagGroup, ...]:
        names = {}
        cards: defaultdict[int, set[int]] = defaultdict(set)
        for ident, name, code in db.execute(
            """SELECT g.id, g.name, t.code FROM tag_groups g LEFT JOIN tags t ON t.tag_group=g.id
               WHERE g.track=? ORDER BY g.name_key, g.id""",
            (track,),
        ):
            names[ident] = name
            if code is not None:
                cards[ident].add(code)
        return tuple(
            TagGroup(ident, name, frozenset(cards[ident])) for ident, name in names.items()
        )

    def add_group(self, track: Track, name: str) -> tuple[TagGroup, ...]:
        name = " ".join(name.split())
        if not 0 < len(name) <= GROUP_NAME_LIMIT:
            raise ValueError(f"Use a group name of 1 to {GROUP_NAME_LIMIT} characters.")
        with self._connect(write=True) as db:
            groups = self._groups(db, track.id)
            if any(group.name.casefold() == name.casefold() for group in groups):
                raise ValueError("A group with that name already exists.")
            if len(groups) >= GROUP_LIMIT:
                raise ValueError(f"A deck can have up to {GROUP_LIMIT} groups.")
            db.execute(
                "INSERT INTO tag_groups(track, name, name_key) VALUES (?, ?, ?)",
                (track.id, name, name.casefold()),
            )
            return self._groups(db, track.id)

    def remove_group(self, track: Track, group: int) -> tuple[TagGroup, ...]:
        with self._connect(write=True) as db:
            db.execute("DELETE FROM tag_groups WHERE id=? AND track=?", (group, track.id))
            return self._groups(db, track.id)

    def set_tags(
        self, track: Track, changes: Mapping[tuple[int, int], bool]
    ) -> tuple[TagGroup, ...]:
        """Apply membership deltas, preserving independent changes from other instances."""
        with self._connect(write=True) as db:
            for (group, code), checked in changes.items():
                if checked:
                    db.execute(
                        """INSERT OR IGNORE INTO tags SELECT id, ? FROM tag_groups
                           WHERE id=? AND track=?""",
                        (code, group, track.id),
                    )
                else:
                    db.execute(
                        """DELETE FROM tags WHERE code=? AND tag_group IN
                           (SELECT id FROM tag_groups WHERE id=? AND track=?)""",
                        (code, group, track.id),
                    )
            return self._groups(db, track.id)
