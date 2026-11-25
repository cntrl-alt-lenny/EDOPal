"""Public events, standings, card lists and deck downloads from Format Library."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote, urlencode

from .deck import Sections, read_ydk
from .http_cache import DeckError, HttpCache, ItemUnavailable

SITE = "https://formatlibrary.com"
PAGE_SIZE = 12
WEEK = 7 * 86400
EVENT_TTL = 6 * 3600
T = TypeVar("T")


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
class EventPage:
    events: tuple[Event, ...]
    has_next: bool
    stale: bool = False


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
    sections: tuple[tuple[tuple[str, int], ...], ...]
    printings: tuple[tuple[tuple[int, str], ...], ...] = ()
    original: Sections = ()
    source: bytes = b""


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _positive(value: object) -> int | None:
    if isinstance(value, (str, int)) and re.fullmatch(r"[0-9]{1,10}", str(value)):
        return int(value) or None
    return None


def _rows(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise DeckError("Format Library returned an unfamiliar response. Open the event on its website.")
    return [row for row in value if isinstance(row, dict)]


def parse_events(obj: object) -> EventPage:
    rows = _rows(obj)
    events = []
    for row in rows:
        code, name = _text(row.get("abbreviation")), _text(row.get("name"))
        if code and name and row.get("display") is not False:
            events.append(
                Event(code, name, _text(row.get("formatName")), _text(row.get("startedAt"))[:10], _positive(row.get("size")))
            )
    return EventPage(tuple(events), len(obj) >= PAGE_SIZE)


def parse_standings(obj: object) -> tuple[Deck, ...]:
    if not isinstance(obj, dict) or "topDecks" not in obj:
        raise DeckError("Format Library returned unfamiliar standings.")
    if isinstance(event := obj.get("event"), dict) and event.get("display") is False:
        raise ItemUnavailable("This event is no longer public on Format Library.")
    decks = {}
    for row in _rows(obj["topDecks"] or []):
        deck_id = _positive(row.get("id"))
        if deck_id and row.get("display") is not False and deck_id not in decks:
            decks[deck_id] = Deck(
                deck_id,
                _text(row.get("deckTypeName")) or "Decklist",
                _text(row.get("builderName")) or "Unknown player",
                _positive(row.get("placement")),
            )
    return tuple(sorted(decks.values(), key=lambda deck: deck.place or 10**10))


def parse_preview(obj: object, deck_id: int) -> DeckPreview:
    if not isinstance(obj, dict) or _positive(obj.get("id")) != deck_id or obj.get("display") is not True:
        raise ItemUnavailable("This decklist isn’t publicly available.")
    counts, printings = [], []
    for section in ("main", "extra", "side"):
        rows = _rows(obj.get(section) or [])
        names = [_text(card.get("name")) for card in rows]
        if not all(names):
            raise DeckError("Format Library returned an unnamed card in this list.")
        counts.append(tuple(Counter(names).items()))
        printings.append(
            tuple((_positive(card.get("artworkId")) or 0, name) for card, name in zip(rows, names, strict=True))
        )
    if not counts[0]:
        raise DeckError("Format Library hasn’t published a main deck for this list.")
    original = ()
    source = b""
    if isinstance(embedded := obj.get("ydk"), str) and len(embedded) <= 32768:
        with suppress(DeckError):
            original = read_ydk(embedded)
            source = embedded.encode("utf-8")
    return DeckPreview(tuple(counts), tuple(printings), original, source)


def _thumbnail(data: bytes) -> bytes:
    if (
        len(data) < 33
        or not data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        or not all(1 <= int.from_bytes(data[i : i + 4], "big") <= 1200 for i in (16, 20))
    ):
        raise DeckError("Format Library has no usable thumbnail for this deck.")
    return data


class FormatLibrary:
    def __init__(self, cache_dir: Path):
        self._http = HttpCache(cache_dir)

    @property
    def stale(self) -> bool:
        """Whether this thread’s most recent response came from stale offline data."""
        return self._http.stale

    def _json(self, path: str, ttl: int, parse: Callable[[object], T]) -> T:
        def decode(body: bytes) -> T:
            try:
                return parse(json.loads(body.decode("utf-8-sig")))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise DeckError("Format Library returned an unreadable response. Try again later.") from exc

        return self._http.read(f"{SITE}/api/{path}", ttl, 1024 * 1024, decode)

    def formats(self) -> tuple[str, ...]:
        def names(obj):
            return tuple(dict.fromkeys(name for row in _rows(obj) if (name := _text(row.get("name")))))

        return self._json("formats", WEEK, names)

    def events(self, format_name: str, page: int = 1) -> EventPage:
        if page < 1:
            raise ValueError("Page numbers start at one.")
        if not format_name or any(c in format_name for c in ",:"):
            raise DeckError("Format Library doesn’t support this format name.")
        query = urlencode(dict(page=page, limit=PAGE_SIZE, sort="startedAt:desc", filter=f"format:eq:{format_name}"))
        result = self._json(f"events?{query}", EVENT_TTL, parse_events)
        return replace(result, stale=self.stale)

    def standings(self, event: Event) -> tuple[Deck, ...]:
        return self._json(f"events/{quote(event.code, safe='')}", EVENT_TTL, parse_standings)

    def preview(self, deck: Deck) -> DeckPreview:
        return self._json(f"decks/{deck.id}", EVENT_TTL, lambda obj: parse_preview(obj, deck.id))

    def thumbnail(self, deck: Deck) -> bytes:
        url = f"https://cdn.formatlibrary.com/images/decks/thumbnails/{deck.id}.png"
        return self._http.read(url, WEEK, 1024 * 1024, _thumbnail)

    def forget_deck(self, deck: Deck) -> None:
        """Reload both representations after a publication changes between preview and save."""
        self._http.forget((f"{SITE}/api/decks/{deck.id}", f"{SITE}/api/decks/download/{deck.id}"))

    def download(self, deck: Deck) -> bytes:
        def checked(body):
            read_ydk(body)
            return body

        return self._http.read(f"{SITE}/api/decks/download/{deck.id}", EVENT_TTL, 32 * 1024, checked)
