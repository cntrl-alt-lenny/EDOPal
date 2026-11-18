"""Public Format Library tournament results and EDOPro deck imports."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from card_databases import iter_installed_printings
from durable_write import durable_write, sync_directory, waiting_lock
from network_tls import create_ssl_context

SITE = "https://formatlibrary.com"
PAGE_SIZE = 12
SECTIONS = ("#main", "#extra", "!side")
MAX_YDK = 32 * 1024


class DeckError(ValueError):
    """A source or deck file problem that can be shown in the browser."""


@dataclass(frozen=True)
class Event:
    code: str
    name: str
    format: str
    date: str
    players: int | None

    @property
    def url(self) -> str:
        return f"{SITE}/events/{quote(self.code, safe='')}"


@dataclass(frozen=True)
class Deck:
    id: int
    name: str
    builder: str
    place: int | None

    @property
    def url(self) -> str:
        return f"{SITE}/decks/{self.id}"


@dataclass(frozen=True)
class DeckPreview:
    # Counts preserve source order, with one entry per distinct card name.
    sections: tuple[tuple[tuple[str, int], ...], ...]
    printings: tuple[tuple[tuple[int, str], ...], ...] = ()
    original: tuple[tuple[str, ...], ...] = ()


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _positive(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return int(text) if re.fullmatch(r"[0-9]{1,10}", text) and int(text) > 0 else None


def _rows(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise DeckError(
            "Format Library returned an unfamiliar result. Open the event on its website."
        )
    return [item for item in value if isinstance(item, dict)]


def _fetch(url: str, *, limit: int) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "EDOPro-HD-Sync (+https://github.com/cntrl-alt-lenny/edopro-hd-cards)"
        },
    )
    try:
        with urlopen(req, timeout=12, context=create_ssl_context()) as response:
            data = response.read(limit + 1)
    except HTTPError as exc:
        messages = {
            403: "This item is unavailable through Format Library’s public browser.",
            404: "Format Library no longer has this item.",
            429: "Format Library is receiving too many requests. Please try again shortly.",
        }
        raise DeckError(
            messages.get(exc.code, f"Format Library returned HTTP {exc.code}. Try again shortly.")
        ) from exc
    except (URLError, OSError) as exc:
        raise DeckError(
            "Couldn’t reach Format Library. Check your connection and try again."
        ) from exc
    if len(data) > limit:
        raise DeckError("Format Library returned a file too large for this view.")
    return data


def _read(path: str, *, limit: int = 1024 * 1024) -> str:
    data = _fetch(f"{SITE}/api/{path}", limit=limit)
    try:
        return data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise DeckError("Format Library returned an unreadable file.") from exc


def _json(path: str):
    try:
        return json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise DeckError("Format Library returned an unexpected page. Try again shortly.") from exc


class FormatLibrary:
    """One public GET per requested page, with a bounded cache for navigation."""

    def __init__(self):
        # Keep each cache scoped to the browser. Closing its window releases the data.
        self.formats = lru_cache(maxsize=1)(self._formats)
        self.events = lru_cache(maxsize=40)(self._events)
        self.standings = lru_cache(maxsize=32)(self._standings)
        self.preview = lru_cache(maxsize=16)(self._preview)
        self.thumbnail = lru_cache(maxsize=8)(self._thumbnail)
        self.download = lru_cache(maxsize=24)(self._download)

    @staticmethod
    def _formats() -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                name for row in _rows(_json("formats")) if (name := _text(row.get("name")))
            )
        )

    @staticmethod
    def _events(format_name: str, query: str, page: int) -> tuple[Event, ...]:
        if page < 1:
            raise ValueError("page must be positive")
        args = {"page": str(page), "limit": str(PAGE_SIZE), "sort": "startedAt:desc"}
        filters = []
        if format_name:
            if "," in format_name or ":" in format_name:
                raise DeckError("This format name isn’t supported by the source’s search.")
            filters.append(f"format:eq:{format_name}")
        if query:
            if "," in query or ":" in query:
                raise DeckError(
                    "Search with a word or abbreviation from the event name. Omit commas and colons."
                )
            filters.append(f"name:or:{query}")
        if filters:
            args["filter"] = ",".join(filters)
        events = []
        for row in _rows(_json("events?" + urlencode(args))):
            code = _text(row.get("abbreviation"))
            name = _text(row.get("name"))
            if not code or not name or row.get("display") is False:
                continue
            events.append(
                Event(
                    code,
                    name,
                    _text(row.get("formatName")),
                    _text(row.get("startedAt"))[:10],
                    _positive(row.get("size")),
                )
            )
        return tuple(events)

    @staticmethod
    def _standings(event: Event) -> tuple[Deck, ...]:
        obj = _json("events/" + quote(event.code, safe=""))
        if not isinstance(obj, dict):
            raise DeckError("Format Library returned unfamiliar standings.")
        decks = []
        seen = set()
        for row in _rows(obj.get("topDecks") or []):
            deck_id = _positive(row.get("id"))
            if not deck_id or deck_id in seen or row.get("display") is False:
                continue
            seen.add(deck_id)
            decks.append(
                Deck(
                    deck_id,
                    _text(row.get("deckTypeName")) or "Decklist",
                    _text(row.get("builderName")) or "Unknown player",
                    _positive(row.get("placement")),
                )
            )
        return tuple(sorted(decks, key=lambda deck: deck.place or 10**10))

    @staticmethod
    def _preview(deck: Deck) -> DeckPreview:
        obj = _json(f"decks/{deck.id}")
        if (
            not isinstance(obj, dict)
            or _positive(obj.get("id")) != deck.id
            or obj.get("display") is not True
        ):
            raise DeckError("This decklist isn’t publicly available.")
        sections = []
        printings = []
        for key in ("main", "extra", "side"):
            rows = _rows(obj.get(key) or [])
            names = (_text(card.get("name")) for card in rows)
            sections.append(tuple(Counter(name for name in names if name).items()))
            printings.append(
                tuple(
                    (_positive(card.get("artworkId")) or 0, _text(card.get("name")))
                    for card in rows
                )
            )
        if not sections[0]:
            raise DeckError("Format Library hasn’t published a main deck for this list.")
        raw = obj.get("ydk")
        original = ()
        if isinstance(raw, str):
            with contextlib.suppress(DeckError):
                original = read_ydk(raw)
        return DeckPreview(tuple(sections), tuple(printings), original)

    @staticmethod
    def _download(deck: Deck) -> tuple[tuple[str, ...], ...]:
        return read_ydk(_read(f"decks/download/{deck.id}", limit=MAX_YDK))

    @staticmethod
    def _thumbnail(deck: Deck) -> bytes:
        data = _fetch(
            f"https://cdn.formatlibrary.com/images/decks/thumbnails/{deck.id}.png",
            limit=1024 * 1024,
        )
        if (
            len(data) < 33
            or not data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            or not 1 <= int.from_bytes(data[16:20], "big") <= 1200
            or not 1 <= int.from_bytes(data[20:24], "big") <= 1200
        ):
            raise DeckError("Format Library has no usable preview image for this deck.")
        return data


def read_ydk(source: str, *, allow_empty_main: bool = False) -> tuple[tuple[str, ...], ...]:
    """Validate card passcodes without imposing modern deck sizes on historical formats."""
    sections: list[list[str]] = [[], [], []]
    current = -1
    for number, raw in enumerate(source.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower in SECTIONS:
            index = SECTIONS.index(lower)
            if current == -1 and index != 0:
                raise DeckError("This decklist is missing its #main section.")
            if index <= current:
                raise DeckError("This decklist has duplicate or out-of-order sections.")
            current = index
        elif line.startswith("#") or (current == -1 and lower.startswith("created by")):
            continue
        elif current >= 0 and re.fullmatch(r"[0-9]{1,10}", line) and 0 < int(line) <= 0xFFFFFFFF:
            sections[current].append(line)
        else:
            raise DeckError(f"Invalid YDK section or passcode on line {number}.")
    if current == -1:
        raise DeckError("This decklist is missing its #main section.")
    if not allow_empty_main and not sections[0]:
        raise DeckError("This decklist has no main-deck cards.")
    return tuple(tuple(cards) for cards in sections)


def _filename(event: Event, deck: Deck) -> str:
    place = f"{deck.place:02} " if deck.place else ""
    title = f"{event.format} - {event.code} - {place}{deck.name} - {deck.builder}"
    title = "".join(
        " " if c in '<>:"/\\|?*' or unicodedata.category(c)[0] == "C" else c for c in title
    )
    # Leave room for the source ID and collision suffix, even with multibyte names.
    title = (
        " ".join(title.split()).encode("utf-8")[:150].decode("utf-8", errors="ignore").strip(". ")
    )
    return f"{title} [FL{deck.id}]"


def deck_printing_names(rows: Iterable[tuple[int, str, int | None]]) -> dict[int, str]:
    """Index playing-deck printings. A later token or script record overrides an earlier card."""
    names = {}
    for code, name, kind in rows:
        if isinstance(kind, int) and (kind & 0x4000 or not kind & 7):
            names.pop(code, None)
        else:
            names[code] = name
    return names


def local_deck_printings(game: Path) -> dict[int, str]:
    return deck_printing_names(iter_installed_printings(game))


def match_local_printings(
    cards: tuple[tuple[str, ...], ...], local: dict[int, str], preview: DeckPreview
) -> tuple[tuple[tuple[str, ...], ...], int]:
    """Keep local deck printings. Confirm replacements for missing or non-deckable export IDs."""
    if preview.original != cards or len(preview.printings) != len(cards):
        return cards, 0
    sections = []
    changed = 0
    for section, printings in zip(cards, preview.printings, strict=True):
        # The detail API can sort cards differently from the downloadable deck.
        # Remove identical passcodes by multiplicity before matching its alt-art IDs.
        original = Counter(int(code) for code in section)
        published = Counter(code for code, _ in printings)
        names: dict[int, set[str]] = {}
        for code, name in printings:
            names.setdefault(code, set()).add(name.casefold())
        replacements = {}
        if len(section) == len(printings) and 0 not in published:
            remaining = published - original
            for variant, copies in (original - published).items():
                plausible = [base for base in remaining if 0 < abs(base - variant) < 10]
                if len(plausible) != 1 or remaining[plausible[0]] < copies:
                    replacements.clear()
                    break
                base = plausible[0]
                remaining[base] -= copies
                if (
                    variant not in local
                    # A single export ID can describe both an exact card and a variant.
                    # Section order cannot tell us which identical copies to change.
                    and variant not in published
                    and base in local
                    and names[base] == {local[base].casefold()}
                ):
                    replacements[variant] = str(base)
        revised = tuple(replacements.get(int(code), code) for code in section)
        sections.append(revised)
        changed += sum(a != b for a, b in zip(section, revised, strict=True))
    return tuple(sections), changed


@dataclass(frozen=True)
class DeckPublication:
    path: Path
    added: bool
    kept: tuple[Path, ...]
    digest: str


def save_deck(
    game_path: str | Path,
    event: Event,
    deck: Deck,
    cards: tuple[tuple[str, ...], ...],
    *,
    goat_versions: bool = False,
) -> tuple[Path, bool]:
    saved = publish_deck(game_path, event, deck, cards, goat_versions=goat_versions)
    return saved.path, saved.added


def publish_deck(
    game_path: str | Path,
    event: Event,
    deck: Deck,
    cards: tuple[tuple[str, ...], ...],
    *,
    goat_versions: bool = False,
) -> DeckPublication:
    """Publish a complete YDK, preserving personal edits and deduplicating repeat imports."""
    title = _filename(event, deck)
    if goat_versions:
        title += " (GOAT versions)"
    header = f"#created by {_text(deck.builder)} · Format Library {deck.url}"
    lines = [header]
    for marker, entries in zip(SECTIONS, cards, strict=True):
        lines.extend((marker, *entries))
    return _publish(game_path, title, ("\n".join(lines) + "\n").encode(), reuse_identical=True)


def publish_ydk(
    game_path: str | Path, title: str, content: bytes, *, reuse_identical: bool = False
) -> tuple[Path, bool]:
    """Publish a complete copy under a portable filename using exclusive creation."""
    saved = _publish(game_path, title, content, reuse_identical=reuse_identical)
    return saved.path, saved.added


def _publish(
    game_path: str | Path, title: str, content: bytes, *, reuse_identical: bool
) -> DeckPublication:
    """Create the file without ever replacing one, and report what was kept beside it."""
    # The backup package also loads the collection engines. Keep ordinary YDK
    # reads and Format Library browsing independent of that import.
    from backup.core import folded, publish_new, safe

    if (
        not title.strip(". ")
        or title != title.rstrip(". ")
        or any(c in '<>:"/\\|?*' or unicodedata.category(c)[0] == "C" for c in title)
        or PureWindowsPath(title + ".ydk").is_reserved()
        or len(title.encode("utf-8")) > 230
    ):
        raise DeckError("Choose a short deck name without reserved characters or folder paths.")
    if not game_path:
        raise DeckError("Choose an EDOPro installation before importing decks.")
    base = Path(game_path).resolve()
    if not base.is_dir():
        raise DeckError("The selected EDOPro folder is unavailable. Choose its location again.")
    folder = (base / "deck").resolve()
    if not folder.is_relative_to(base):
        raise DeckError("The deck folder points outside the selected EDOPro installation.")
    digest = hashlib.sha256(content).hexdigest()
    kept: list[Path] = []
    # Exclusive rename protects the exact name. The game lock also serializes
    # case-folded name selection by Studio writers on case-sensitive volumes.
    with waiting_lock(safe(base, ".edopro-appearance.lock")):
        if not folder.is_dir():
            folder.mkdir(exist_ok=True)
            sync_directory(folder.parent)
        existing: dict[str, list[Path]] = defaultdict(list)
        for path in sorted(folder.iterdir()):
            existing[folded(path.name)].append(path)
        for i in range(1, 1001):
            suffix = f" ({i})" if i > 1 else ""
            target = folder / f"{title}{suffix}.ydk"
            collisions = existing[folded(target.name)]
            if not collisions:
                try:
                    durable_write(target, content, publish=publish_new)
                    return DeckPublication(target, True, tuple(kept), digest)
                except FileExistsError:
                    collisions.append(target)
            for collision in collisions:
                if reuse_identical and collision.is_file() and not collision.is_symlink():
                    with contextlib.suppress(OSError), collision.open("rb") as original:
                        if original.read(len(content) + 1) == content:
                            return DeckPublication(collision, False, tuple(kept), digest)
                kept.append(collision)
        raise DeckError("Too many different copies of this deck are already in the deck folder.")
