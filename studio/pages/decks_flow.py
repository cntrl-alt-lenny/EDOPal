"""Selection, import receipts and reversible writes for tournament deck browsers."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path
from threading import Event as CancelEvent

from artwork_flow import valid_decks
from card_databases import installed_card_names
from goat_versions import GoatVersions, installed_aliases, installed_whitelist
from music.settings import _game_running
from tournament_decks import (
    PAGE_SIZE,
    Deck,
    DeckError,
    DeckPublication,
    Event,
    FormatLibrary,
    match_local_printings,
    publish_deck,
)

ALL_FORMATS = "All formats"
STARTER_FORMATS = (ALL_FORMATS, "Goat", "Edison", "Tengu", "HAT", "Perfect Circle")
GOAT_ROOM_SETTINGS = (
    "When hosting in EDOPro, set Forbidden List to 2005.4 GOAT, Rule to GOAT and "
    "Allowed Cards to Anything goes. Keep both deck checks enabled."
)


def format_choices(names: tuple[str, ...]) -> tuple[str, ...]:
    if not names:
        return STARTER_FORMATS
    favorites = (name for name in STARTER_FORMATS[1:] if name in names)
    extras = sorted(set(names) - set(STARTER_FORMATS), key=lambda name: (name.casefold(), name))
    return (ALL_FORMATS, *favorites, *extras)


@dataclass(frozen=True)
class EventPage:
    format: str = "Goat"
    query: str = ""
    number: int = 1

    @classmethod
    def search(cls, name: str, query: str) -> "EventPage":
        return cls("" if name == ALL_FORMATS else name, query.strip())

    def previous(self) -> "EventPage | None":
        if self.number > 1:
            return EventPage(self.format, self.query, self.number - 1)
        return None

    def following(self, events: tuple[Event, ...]) -> "EventPage | None":
        if len(events) == PAGE_SIZE:
            return EventPage(self.format, self.query, self.number + 1)
        return None


@dataclass(frozen=True)
class ImportBatch:
    game: Path
    event: Event
    decks: tuple[Deck, ...]
    historical: bool = False


@dataclass
class StandingsSelection:
    event: Event | None = None
    decks: tuple[Deck, ...] = ()
    selected: tuple[Deck, ...] = ()

    def open(self, event: Event | None) -> None:
        self.event = event
        self.decks = self.selected = ()

    def populate(self, decks: tuple[Deck, ...]) -> None:
        self.decks = decks
        self.selected = decks[:1]

    def select(self, ids: set[int]) -> None:
        self.selected = tuple(deck for deck in self.decks if deck.id in ids)

    @property
    def can_select_all(self) -> bool:
        return len(self.decks) > 1 and self.selected != self.decks

    @property
    def preview(self) -> Deck | None:
        return self.selected[0] if len(self.selected) == 1 else None

    def capture(self, game: Path, historical: bool) -> ImportBatch | None:
        if self.event and self.selected:
            return ImportBatch(game, self.event, self.selected, historical and is_goat(self.event))
        return None


def is_goat(event: Event) -> bool:
    return event.format.casefold() == "goat"


class GameOpen(DeckError):
    """The selected installation is open or its process could not be checked."""


def require_game_closed(game: Path) -> None:
    try:
        opened = _game_running(game)
    except OSError as exc:
        raise GameOpen(
            "Couldn’t check whether EDOPro is open. Close EDOPro, then try again."
        ) from exc
    if opened:
        raise GameOpen("Close EDOPro, then choose Retry.")


@dataclass(frozen=True)
class GoatMatch:
    deck: Deck
    card: str
    input_id: int
    saved_id: int
    reason: str


@dataclass(frozen=True)
class ImportEntry:
    deck: Deck
    saved: DeckPublication | None = None
    reason: str = ""


@dataclass(frozen=True)
class ImportProgress:
    done: int
    total: int
    deck: Deck | None = None


@dataclass(frozen=True)
class ImportReceipt:
    game: Path
    entries: tuple[ImportEntry, ...]
    stopped: bool
    blocked: str
    matched: int
    unresolved: tuple[int, ...]
    historical: bool
    historical_changes: int
    review: tuple[GoatMatch, ...]
    indexed: bool

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(entry.saved.path for entry in self.entries if entry.saved)

    @property
    def created(self) -> tuple[DeckPublication, ...]:
        return tuple(entry.saved for entry in self.entries if entry.saved and entry.saved.added)

    @property
    def skipped(self) -> int:
        return sum(entry.saved is None for entry in self.entries)


def import_decks(
    batch: ImportBatch,
    library: FormatLibrary,
    cancel: CancelEvent,
    progress: Callable[[ImportProgress], None],
) -> ImportReceipt:
    """Stop between decks or before publication, retaining every completed result."""
    game = batch.game.resolve()
    entries: list[ImportEntry] = []
    review: list[GoatMatch] = []
    unresolved: set[int] = set()
    matched = goat_changes = 0
    blocked = ""
    local: dict[int, str] = {}
    goat = None
    historical = batch.historical and is_goat(batch.event)
    try:
        if not cancel.is_set():
            require_game_closed(game)
            local = installed_card_names(game)
            if historical:
                limits = installed_whitelist(game)
                if not limits or not local:
                    raise DeckError(
                        "Open EDOPro to update its cards and 2005.4 GOAT list, or turn off "
                        "GOAT card versions to save the published lists."
                    )
                goat = GoatVersions(limits, local, installed_aliases(game, local))
    except (OSError, DeckError) as exc:
        blocked = str(exc)

    for deck in batch.decks:
        if blocked or cancel.is_set():
            break
        progress(ImportProgress(len(entries), len(batch.decks), deck))
        try:
            cards = library.download(deck)
            if cancel.is_set():
                break
            changed = 0
            if local and any(int(code) not in local for section in cards for code in section):
                with suppress(DeckError):
                    cards, changed = match_local_printings(cards, local, library.preview(deck))
            conversion = goat.convert(cards) if goat else None
            if conversion:
                cards = conversion.sections
            if cancel.is_set():
                break
            require_game_closed(game)
            if cancel.is_set():
                break
            saved = publish_deck(game, batch.event, deck, cards, goat_versions=historical)
        except GameOpen as exc:
            blocked = str(exc)
            break
        except (DeckError, OSError) as exc:
            entries.append(ImportEntry(deck, reason=str(exc)))
            break
        except Exception:
            # A malformed source or unexpected failure must not hide earlier writes.
            entries.append(ImportEntry(deck, reason="Something went wrong while saving this deck."))
            break

        entries.append(ImportEntry(deck, saved))
        matched += changed
        if local:
            unresolved.update(
                int(code) for section in cards for code in section if int(code) not in local
            )
        if conversion:
            goat_changes += conversion.changed
            review.extend(
                GoatMatch(deck, local[code], code, int(target), "Historical version")
                for code, target in conversion.replacements.items()
            )
            review.extend(
                GoatMatch(deck, local.get(code, "Unknown card"), code, code, reason)
                for code, reason in conversion.review.items()
            )
        progress(ImportProgress(len(entries), len(batch.decks)))

    remaining = batch.decks[len(entries) :]
    stopped = bool(remaining) and cancel.is_set()
    reason = blocked or ("Cancelled before saving." if stopped else "Skipped after a save failed.")
    entries.extend(ImportEntry(deck, reason=reason) for deck in remaining)
    return ImportReceipt(
        game,
        tuple(entries),
        stopped,
        blocked,
        matched,
        tuple(sorted(unresolved)),
        historical,
        goat_changes,
        tuple(review),
        bool(local),
    )


def resume_import(
    receipt: ImportReceipt,
    batch: ImportBatch,
    library: FormatLibrary,
    cancel: CancelEvent,
    progress: Callable[[ImportProgress], None],
) -> ImportReceipt:
    completed = tuple(entry for entry in receipt.entries if entry.saved)
    remaining = tuple(entry.deck for entry in receipt.entries if not entry.saved)

    def advance(update: ImportProgress) -> None:
        progress(ImportProgress(update.done + len(completed), len(batch.decks), update.deck))

    retry = import_decks(
        ImportBatch(batch.game, batch.event, remaining, batch.historical), library, cancel, advance
    )
    return ImportReceipt(
        receipt.game,
        completed + retry.entries,
        retry.stopped,
        retry.blocked,
        receipt.matched + retry.matched,
        tuple(sorted(set(receipt.unresolved) | set(retry.unresolved))),
        retry.historical,
        receipt.historical_changes + retry.historical_changes,
        receipt.review + retry.review,
        receipt.indexed or retry.indexed,
    )


def artwork_paths(
    selected: str, receipt: ImportReceipt, removed: frozenset[Path] = frozenset()
) -> tuple[Path, ...]:
    paths = tuple(path for path in receipt.paths if path not in removed)
    if not selected or not valid_decks(selected, receipt.game, paths):
        raise DeckError(
            "The saved decks or installation have moved. Save the decks again to get their artwork."
        )
    require_game_closed(receipt.game)
    return paths


@dataclass(frozen=True)
class UndoResult:
    removed: tuple[Path, ...]
    kept: tuple[Path, ...]
    pending: tuple[DeckPublication, ...]
    blocked: str = ""


def undo_import(
    selected: str, receipt: ImportReceipt, created: tuple[DeckPublication, ...]
) -> UndoResult:
    """Remove only unchanged copies created by this receipt."""
    game = receipt.game
    if not selected or Path(selected).resolve() != game:
        raise DeckError("Select the installation where these decks were saved.")
    folder = (game / "deck").resolve()
    if not folder.is_relative_to(game):
        raise DeckError("The deck folder has moved outside the selected installation.")
    removed, kept, pending = [], [], []
    for index, entry in enumerate(created):
        path = entry.path
        try:
            require_game_closed(game)
            if path.is_symlink() or not path.is_file() or path.resolve().parent != folder:
                kept.append(path)
                continue
            before = path.stat()
            with path.open("rb") as source:
                digest = file_digest(source, "sha256").hexdigest()
            after = path.stat()
            # Reading a deck can update its access time. Only compare write and identity metadata.
            original = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            current = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if digest != entry.digest or path.is_symlink() or original != current:
                kept.append(path)
                continue
            path.unlink()
            removed.append(path)
        except GameOpen as exc:
            return UndoResult(tuple(removed), tuple(kept), (*pending, *created[index:]), str(exc))
        except OSError:
            pending.append(entry)
    return UndoResult(tuple(removed), tuple(kept), tuple(pending))
