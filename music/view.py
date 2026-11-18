"""Toolkit-free browsing. Run refresh on a worker; query only touches the published snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Generic, TypeVar

from music.audio import _scene
from music.catalogue import LOCAL, SCENES, Game, catalog, library_root, normalized
from music.digest_cache import browsing_digests
from music.discovery import Track, scan_scene

T = TypeVar("T")


@dataclass(frozen=True)
class JobResult(Generic[T]):
    value: T | None = None
    error: str = ""


def run_job(action: Callable[[], T]) -> JobResult[T]:
    """Capture a terminal result at the boundary of a UI worker."""
    try:
        return JobResult(value=action())
    except Exception as exc:
        return JobResult(error=str(exc) or type(exc).__name__)


@dataclass(frozen=True)
class Album:
    game: Game
    counts: Mapping[str, int]


@dataclass(frozen=True)
class Selection:
    games: tuple[Album, ...]
    game: str | None
    tracks: tuple[Track, ...]
    track: Track | None


@dataclass(frozen=True)
class _Snapshot:
    albums: tuple[Album, ...] = ()
    rows: dict[tuple[str, str], tuple[Track, ...]] = field(default_factory=dict)
    terms: dict[str, tuple[str, ...]] = field(default_factory=dict)


class LibraryView:
    def __init__(self, installation: Path, *, root: Path | None = None) -> None:
        self.installation = installation
        self.root = root or library_root()
        self._snapshot = _Snapshot()

    def refresh(self, *, checkpoint: Callable[[], None] | None = None) -> None:
        games = catalog(root=self.root)
        groups: dict[tuple[str, str], list[Track]] = {}
        with browsing_digests(self.root, checkpoint) as digest:
            for scene in SCENES:
                if checkpoint:
                    checkpoint()
                for row in scan_scene(
                    self.installation, scene, self.root, games, digest_file=digest
                ):
                    if row.game not in games:
                        games[row.game] = Game(row.game, row.game)
                    groups.setdefault((scene, row.game), []).append(row)
        # Preserve playlist order. Available recordings follow in title order.
        rows = {
            key: tuple(
                sorted(
                    value,
                    key=lambda t: (not t.in_playlist, "" if t.in_playlist else t.title.casefold()),
                )
            )
            for key, value in groups.items()
        }
        albums = []
        terms = {}
        for game in games.values():
            counts = {scene: len(rows.get((scene, game.id), ())) for scene in SCENES}
            albums.append(Album(game, MappingProxyType(counts)))
            fields = [
                game.title,
                game.platform,
                str(game.first_year),
                str(game.year),
                *game.aliases,
            ]
            fields.extend(row.title for scene in SCENES for row in rows.get((scene, game.id), ()))
            terms[game.id] = tuple(normalized(f).replace(" ", "") for f in fields)
        self._snapshot = _Snapshot(tuple(albums), rows, terms)

    def query(
        self,
        scene: str,
        search: str = "",
        *,
        audio_only: bool = False,
        series: str | None = None,
        game: str | None = None,
        track: Path | None = None,
    ) -> Selection:
        _scene(scene)
        snap = self._snapshot
        needle = normalized(search).replace(" ", "")
        albums = [
            album
            for album in snap.albums
            if (album.counts[scene] or not (audio_only or album.game.id == LOCAL))
            and (not series or album.game.series == series)
            and any(needle in field for field in snap.terms[album.game.id])
        ]
        albums.sort(
            key=lambda a: (
                not a.counts[scene],
                a.game.id == LOCAL,
                a.game.first_year,
                a.game.title.casefold(),
            )
        )
        keys = [album.game.id for album in albums]
        if game not in keys:
            game = next(
                (
                    key
                    for key in keys
                    if any(row.in_playlist for row in snap.rows.get((scene, key), ()))
                ),
                keys[0] if keys else None,
            )
        group = snap.rows.get((scene, game), ()) if game else ()
        first = next(
            (row for row in group if needle and needle in normalized(row.title).replace(" ", "")),
            group[0] if group else None,
        )
        selected = next((row for row in group if row.path == track), first)
        return Selection(tuple(albums), game, group, selected)


@dataclass
class SeekState:
    """Hold a drag position until release, while the playback clock continues to advance."""

    fraction: float | None = None

    def drag(self, fraction: float) -> None:
        self.fraction = min(1.0, max(0.0, fraction))

    def keyboard(self, key: str, position: float, duration: float) -> None:
        if duration <= 0:
            return
        if key in ("Home", "End"):
            self.drag(float(key == "End"))
        else:
            base = self.position(position, duration)
            self.drag((base + (5 if key in ("Right", "Up") else -5)) / duration)

    def position(self, position: float, duration: float) -> float:
        return position if self.fraction is None else self.fraction * duration

    def release(self, duration: float) -> float | None:
        value = None if self.fraction is None else self.fraction * duration
        self.fraction = None
        return value
