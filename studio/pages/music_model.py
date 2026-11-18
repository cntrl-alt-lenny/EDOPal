"""Cross-scene album browsing and immutable playlist snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import music_library as music
from music.catalogue import Game, normalized
from music.discovery import Track
from music.importing import ImportItem
from music.view import LibraryView

SCENES = {
    "menu": ("Main menu", "library"),
    "deck": ("Deck editor", "deck"),
    "duel": ("During a duel", "cards"),
    "advantage": ("When you’re ahead", "coverage"),
    "disadvantage": ("When you’re behind", "sounds"),
    "win": ("After a win", "decks"),
    "lose": ("After a loss", "music"),
}
SOURCES = {
    "installed": "Installed in game",
    "parked": "Original game music",
    "personal": "Your library",
    "bundled": "Bundled music",
}


def track_count(n: int) -> str:
    return f"{n} track" if n == 1 else f"{n} tracks"


def term(value: str) -> str:
    return normalized(value).replace(" ", "")


def match_description(item: ImportItem, chosen_album: bool) -> str:
    if item.error:
        return "Unreadable"
    if item.path.suffix.lower() not in music.FORMATS:
        return "Unsupported"
    if item.duplicate:
        return "Found in library" if item.in_library else "Repeated file"
    if chosen_album:
        return "Chosen album"
    return {"high": "Strong match", "medium": "Suggested", "low": "Check game"}[item.confidence]


def needs_game(item: ImportItem, chosen_album: bool) -> bool:
    return (
        item.game is None
        and not item.error
        and item.path.suffix.lower() in music.FORMATS
        and (chosen_album or not item.in_library)
    )


@dataclass(frozen=True)
class Album:
    game: Game
    tracks: tuple[Track, ...]
    terms: tuple[str, ...]

    @property
    def edition(self) -> str:
        return (
            "  ·  ".join(filter(None, (self.game.platform, str(self.game.year or ""))))
            or "Personal collection"
        )


@dataclass(frozen=True)
class Scene:
    tracks: tuple[Track, ...]
    undo: bool
    restore: bool


@dataclass(frozen=True)
class Library:
    albums: dict[str, Album]
    scenes: dict[str, Scene]
    enabled: bool | None

    def browse(self, search: str, series: str = "", has_music: bool = False) -> list[Album]:
        needle = term(search)
        return [
            a
            for a in self.albums.values()
            if (not has_music or a.tracks)
            and (not series or a.game.series == series)
            and any(needle in field for field in a.terms)
        ]


def read_library(game: Path, root: Path, checkpoint: Callable[[], None]) -> Library:
    view = LibraryView(game, root=root)
    view.refresh(checkpoint=checkpoint)
    grouped: dict[Game, dict[str, Track]] = {}
    titles: dict[str, set[str]] = {}
    scenes = {}
    for scene in SCENES:
        checkpoint()
        scenes[scene] = Scene(
            tuple(music.playlist(game, scene, root=root)),
            music.can_undo(game, scene),
            music.can_restore(game, scene),
        )
        for entry in view.query(scene).games:
            recordings = grouped.setdefault(entry.game, {})
            if entry.counts[scene]:
                for row in view.query(scene, game=entry.game.id).tracks:
                    titles.setdefault(entry.game.id, set()).add(row.title)
                    # Keep a stable library source when an identical copy is installed elsewhere.
                    if row.digest not in recordings or row.source == "personal":
                        recordings[row.digest] = row
    albums = {}
    for item, recordings in sorted(
        grouped.items(),
        key=lambda pair: (
            pair[0].series == "Personal",
            pair[0].series,
            not pair[1],
            not pair[0].cover_page,
            pair[0].first_year,
            pair[0].title.casefold(),
        ),
    ):
        tracks = tuple(sorted(recordings.values(), key=lambda r: r.title.casefold()))
        fields = (
            item.title,
            item.platform,
            str(item.year),
            str(item.first_year),
            *item.aliases,
            *titles.get(item.id, ()),
        )
        albums[item.id] = Album(item, tracks, tuple(term(f) for f in fields))
    return Library(albums, scenes, music.music_enabled(game))


def change_order(tracks: tuple[Track, ...], index: int, step: int) -> list[Track]:
    result = list(tracks)
    other = index + step
    if 0 <= index < len(result) and 0 <= other < len(result):
        result[index], result[other] = result[other], result[index]
    return result
