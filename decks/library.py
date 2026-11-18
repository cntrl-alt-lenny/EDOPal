"""Incremental, installation-scoped deck indexing and in-memory search."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock

from tournament_decks import MAX_YDK, read_ydk

from .banlists import Banlist, parse_lists
from .cards import Catalog, load_catalog
from .model import Counts, SavedDeck, Sections, preview
from .sources import bounded_text, discover, identity


@dataclass(frozen=True)
class CardMatch:
    deck: SavedDeck
    counts: Counts
    card_ids: tuple[int, ...]


class Library:
    """A completed scan. Treat returned snapshots as read-only, including their mappings."""

    def __init__(
        self,
        game: Path,
        decks: tuple[SavedDeck, ...],
        catalog: Catalog,
        banlists: tuple[Banlist, ...],
        issues: tuple[str, ...],
        *,
        deck_scan_complete: bool = True,
    ) -> None:
        self.game = game
        self.decks = decks
        self.catalog = catalog
        self.banlists = banlists
        self.issues = issues
        self.deck_scan_complete = deck_scan_complete
        self._postings: defaultdict[int, list[tuple[int, Counts]]] = defaultdict(list)
        for index, deck in enumerate(decks):
            if deck.preview:
                for code, counts in deck.preview.copies.items():
                    self._postings[code].append((index, counts))
        self._names = {
            code: catalog.cards[code].name.casefold()
            for code in self._postings
            if code in catalog.cards
        }

    def search(self, name: str = "", folder: str = "") -> tuple[SavedDeck, ...]:
        name, folder = name.casefold(), folder.replace("\\", "/").casefold()
        return tuple(
            deck
            for deck in self.decks
            if name in deck.name.casefold() and folder in deck.folder.as_posix().casefold()
        )

    def by_card(self, query: str | int) -> tuple[CardMatch, ...]:
        """Find an exact passcode or a name substring. Sum matching prints per deck."""
        term = str(query).strip().casefold()
        if not term:
            return ()
        if term.isascii() and term.isdigit():
            codes = [int(term)] if len(term) <= 10 else []
        else:
            codes = [code for code, name in self._names.items() if term in name]
        matches: defaultdict[int, dict[int, Counts]] = defaultdict(dict)
        for code in codes:
            for index, counts in self._postings.get(code, ()):
                matches[index][code] = counts
        return tuple(
            CardMatch(
                self.decks[i], Counts(*map(sum, zip(*rows.values(), strict=True))), tuple(rows)
            )
            for i, rows in sorted(matches.items())
        )


def _stamp(paths: list[Path]) -> tuple:
    result = []
    for path in paths:
        try:
            result.append((path, identity(path)))
        except OSError:
            result.append((path, None))
    return tuple(result)


def _deck_paths(folder: Path, issues: list[str]) -> Iterator[Path]:
    try:
        root = folder.resolve()
        if not root.is_relative_to(folder.parent):
            raise ValueError(f"Deck folder points outside the installation: {folder}")
        if not folder.exists():
            return
        for current, dirs, files in os.walk(folder, onerror=lambda err: issues.append(str(err))):
            base = Path(current)
            # Junctions can form cycles on Windows, including within this installation.
            resolved = base.resolve()
            children = []
            for name in sorted(dirs):
                path = base / name
                try:
                    if path.resolve() == resolved / name:
                        children.append(name)
                    else:
                        issues.append(f"Skipped linked deck directory: {path}")
                except (OSError, RuntimeError) as exc:
                    issues.append(f"{path}: {exc}")
            dirs[:] = children
            yield from (base / name for name in sorted(files) if name.lower().endswith(".ydk"))
    except (OSError, RuntimeError, ValueError) as exc:
        issues.append(str(exc))


class DeckIndexer:
    """Retain one instance per installation to reuse unchanged cards, lists and decks."""

    def __init__(self, game: Path) -> None:
        self.game = Path(game)
        self._lock = Lock()
        self._catalog_key: tuple | None = None
        self._catalog = Catalog({}, {}, set(), ())
        self._decks: dict[Path, tuple[tuple, SavedDeck]] = {}
        self._list_key: tuple | None = None
        self._lists: tuple[Banlist, ...] = ()
        self._list_errors: tuple[str, ...] = ()

    def scan_async(
        self, executor: ThreadPoolExecutor, on_deck: Callable[[SavedDeck], None] | None = None
    ) -> Future[Library]:
        """Submit to a thread executor. on_deck runs on that worker as entries become ready."""
        return executor.submit(self.scan, on_deck)

    def scan(
        self, on_deck: Callable[[SavedDeck], None] | None = None, *, cancel: Event | None = None
    ) -> Library:
        """Blocking CLI/worker entry point. Concurrent scans of this index serialize."""
        with self._lock:
            return self._scan(on_deck, cancel)

    def _scan(self, on_deck: Callable[[SavedDeck], None] | None, cancel: Event | None) -> Library:
        _check_cancelled(cancel)
        game = self.game.resolve()
        if not game.is_dir():
            raise ValueError("The selected installation is unavailable")
        sources = discover(game)
        journals = [
            Path(str(path) + ext) for path in sources.databases for ext in ("-wal", "-journal")
        ]
        catalog_key = (
            tuple(sources.issues),
            _stamp(
                sources.databases
                + sources.strings
                + sources.mappings
                + [p for p in journals if p.exists()]
            ),
        )
        if self._catalog_key != catalog_key or self._catalog.issues:
            self._catalog = load_catalog(sources, cancel=cancel)
            self._catalog_key = catalog_key
            self._decks.clear()

        list_key = _stamp(sources.lists)
        if self._list_key != list_key or self._list_errors:
            lists, errors = [], []
            for path in sources.lists:
                _check_cancelled(cancel)
                try:
                    lists.extend(parse_lists(path))
                except (ValueError, OSError) as exc:
                    errors.append(f"{path}: {exc}")
            self._list_key, self._lists, self._list_errors = list_key, tuple(lists), tuple(errors)

        folder = game / "deck"
        issues = [*dict.fromkeys((*sources.issues, *self._catalog.issues, *self._list_errors))]
        deck_issues: list[str] = []
        updated: dict[Path, tuple[tuple, SavedDeck]] = {}
        decks: list[SavedDeck] = []
        for path in _deck_paths(folder, deck_issues):
            _check_cancelled(cancel)
            relative = path.parent.relative_to(folder)
            stamp = ()
            try:
                if not path.resolve().is_relative_to(folder.resolve()):
                    raise ValueError("Deck points outside the deck folder")
                stamp = identity(path)
                cached = self._decks.get(path)
                if cached and cached[0] == stamp:
                    deck = cached[1]
                else:
                    try:
                        raw = read_ydk(bounded_text(path, MAX_YDK), allow_empty_main=True)
                        if identity(path) != stamp:
                            raise OSError("The deck changed during scanning. Refresh to try again.")
                        sections = Sections(*(tuple(map(int, section)) for section in raw))
                        file_id = (
                            (stamp[0], stamp[1]) if stamp[1] and not path.is_symlink() else None
                        )
                        deck = SavedDeck(
                            path,
                            relative,
                            stamp[3],
                            preview(sections, self._catalog),
                            file_id=file_id,
                        )
                    except ValueError as exc:
                        deck = SavedDeck(path, relative, stamp[3], None, str(exc))
                updated[path] = (stamp, deck)
            except (OSError, RuntimeError, ValueError) as exc:
                deck = SavedDeck(path, relative, stamp[3] if stamp else 0, None, str(exc))
            decks.append(deck)
            if on_deck:
                on_deck(deck)
        _check_cancelled(cancel)
        self._decks = updated
        return Library(
            game,
            tuple(decks),
            self._catalog,
            self._lists,
            (*issues, *deck_issues),
            deck_scan_complete=not deck_issues,
        )


def _check_cancelled(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Deck scan cancelled.")
