"""Music packs, EDOPro's stock role pools, and reversible role assignments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from edopal.core import Core
from edopal.core.install import Install, RunState
from edopal.core.jobs import JobContext
from edopal.core.locks import file_lock
from edopal.core.packs import PackInfo
from edopal.core.storage import GameRunningError

ROLES = {"menu": "Main Menu", "deck": "Deck Building", "duel": "Duel", "win": "Victory", "lose": "Defeat"}
YOUR_MUSIC = "your-music"
AUDIO = frozenset({".mp3", ".ogg", ".flac", ".wav"})
_BGM = "sound/BGM"
_REGISTRY = "sound/.edopal-music.json"
_CONF = "config/system.conf"
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHA = re.compile(r"[0-9a-f]{64}")
_MUSIC_FLAG = re.compile(r"(?m)^[ \t]*enable_music[ \t]*=[ \t]*([01])[ \t]*(?:[#;][^\r\n]*)?\r?$")


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    duration_seconds: float = 0
    path: str = ""
    suggested_roles: tuple[str, ...] = ()
    role: str = ""


@dataclass(frozen=True)
class Album:
    id: str
    name: str
    game: str = ""
    year: int | None = None
    platform: str = ""
    cover: Path | None = None
    tracks: tuple[Track, ...] = ()
    pack: PackInfo | None = None
    track_count: int = 0
    cached: bool = False
    install_root: Path | None = None
    series: str = ""
    builtin_titles: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GameState:
    install: Install | None
    local: Album
    assignments: dict[str, str]
    music_off: bool
    can_restore: bool
    pools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    conflicts: tuple[str, ...] = ()
    managed_roles: tuple[str, ...] = ()
    sources: dict[str, list[str]] = field(default_factory=dict)
    managed_paths: dict[str, str] = field(default_factory=dict)


class ConfirmClosedError(GameRunningError):
    """The install service could not determine whether EDOPro is open."""


@dataclass(frozen=True)
class _Owned:
    filename: str
    title: str
    sha256: str
    album_id: str = ""
    track_id: str = ""


def _digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _check_cancel(ctx: JobContext) -> None:
    if ctx.cancelled:
        raise CancelledError("Music operation cancelled.")


def _safe_parts(relative: str) -> tuple[str, ...]:
    if (
        not isinstance(relative, str)
        or any(char in relative for char in ("\\", ":", "\x00"))
        or any(part in ("", ".", "..") for part in relative.split("/"))
    ):
        raise ValueError("Invalid relative music path.")
    return tuple(relative.split("/"))


def _path(root: Path, relative: str) -> Path:
    path = root.resolve()
    for part in _safe_parts(relative):
        path = path / part
        if path.is_symlink() or (os.name == "nt" and path.resolve() != path):
            raise ValueError(f"Linked music path: {path.name}")
    return path


def _slug(value: str) -> str:
    if not isinstance(value, str) or len(value) > 96 or not _SLUG.fullmatch(value):
        raise ValueError("Invalid music pack or track identifier.")
    return value


def _tracks(rows) -> tuple[Track, ...]:
    tracks = []
    seen = set()
    for row in rows:
        track_id = _slug(row["id"])
        relative = row["path"]
        _safe_parts(relative)
        if track_id in seen or Path(relative).suffix.lower() not in AUDIO:
            raise ValueError("Invalid audio track in the music pack.")
        seen.add(track_id)
        tracks.append(
            Track(
                track_id,
                str(row["title"]),
                float(row.get("duration_seconds", 0)),
                relative,
                tuple(role for role in row.get("suggested_roles", ()) if role in ROLES),
            )
        )
    return tuple(tracks)


def _manifest(pack: PackInfo, folder: Path) -> dict:
    with _path(folder, "manifest.json").open(encoding="utf-8") as file:
        data = json.load(file)
    if any(
        data.get(key) != expected
        for key, expected in (("id", pack.id), ("kind", "music"), ("schema", 1), ("version", pack.version))
    ):
        raise ValueError(f"The downloaded {pack.name} pack does not match its catalog entry.")
    return data


def _verified(folder: Path, relative: str, files: dict) -> Path:
    path = _path(folder, relative)
    digest = files.get(relative, "")
    if not isinstance(digest, str) or not _SHA.fullmatch(digest) or _digest(path) != digest:
        raise ValueError(f"Music pack verification failed for {path.name}.")
    return path


def _registry(install: Install) -> dict[str, _Owned]:
    try:
        record = json.loads(_path(install.root, _REGISTRY).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    try:
        if record["schema"] != 1 or record["owner"] != "edopal.music":
            raise ValueError
        owned = {role: _Owned(**row) for role, row in record["roles"].items()}
        for role, item in owned.items():
            if item.album_id or item.track_id:
                _slug(item.album_id)
                _slug(item.track_id)
            if (
                role not in ROLES
                or not re.fullmatch(r"edopal-[a-z0-9-]+\.(ogg|wav|mp3|flac)", item.filename)
                or not _SHA.fullmatch(item.sha256)
                or not isinstance(item.title, str)
            ):
                raise ValueError
    except (AttributeError, TypeError, KeyError, ValueError) as exc:
        raise ValueError("The saved music assignments are unreadable.") from exc
    return owned


def _persist(changes, owned: dict[str, _Owned]) -> None:
    if owned:
        payload = {"schema": 1, "owner": "edopal.music", "roles": {role: asdict(item) for role, item in owned.items()}}
        changes.write(_REGISTRY, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    else:
        changes.remove(_REGISTRY)


def _originals(install: Install, owned: dict[str, _Owned]) -> Iterator[Track]:
    for subdir in (*ROLES, "advantage", "disadvantage", ""):
        role = subdir or "duel"
        try:
            folder = _path(install.root, f"{_BGM}/{subdir}" if subdir else _BGM)
        except ValueError:  # Linked folders can belong to another installation.
            continue
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if path.suffix.lower() not in AUDIO or path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(install.root.resolve()).as_posix()
            stem = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")[:56].rstrip("-") or "track"
            track_id = f"{stem}-{hashlib.sha256(os.fsencode(relative)).hexdigest()[:12]}"
            managed = owned.get(subdir)
            title = managed.title if managed and managed.filename == path.name else path.stem.replace("_", " ")
            yield Track(track_id, title, path=relative, role=role)


class MusicService:
    def __init__(self, core: Core):
        self.core = core
        self._pack_lock = threading.Lock()

    def catalog(self, ctx: JobContext) -> tuple[Album, ...]:
        packs = self.core.packs.catalog("music")
        albums = []
        for pack in packs:
            _check_cancel(ctx)
            _slug(pack.id)
            try:
                cover = self.core.packs.thumbnail(pack)
            except (OSError, ValueError, RuntimeError):
                cover = None
            meta = pack.meta
            tracks = _tracks(meta.get("tracks", ()))
            album = Album(
                pack.id,
                pack.name,
                meta.get("game", pack.name),
                meta.get("year"),
                meta.get("platform", ""),
                cover,
                tracks,
                pack,
                meta.get("track_count", len(tracks)),
                bool(self.core.packs.cached(pack.id)),
                series=meta.get("series", ""),
            )
            if album.id == "music-edopro-default":
                try:
                    data = _manifest(pack, self._ensure(pack, ctx))
                    titles = {row["path"]: row["title"] for rows in data.get("builtin_tracks", {}).values() for row in rows}
                    album = replace(album, builtin_titles=titles)
                except (ValueError, OSError, RuntimeError, KeyError):
                    _check_cancel(ctx)
            if album.cached and not tracks and album.track_count:
                album = self.load_album(album, ctx)
            albums.append(album)
        return tuple(albums)

    def banners(self, ctx):
        pack = next((p for p in self.core.packs.catalog("art") if p.id == "art-music-banners"), None)
        if pack is None:
            return {}
        folder = self._ensure(pack, ctx)
        files = json.loads(_path(folder, "manifest.json").read_text(encoding="utf-8"))["files"]
        banners = {}
        for relative in files:
            path = Path(relative)
            if path.parent == Path("banners") and path.suffix.lower() in {".jpg", ".jpeg", ".webp", ".png"}:
                banners["music-" + path.stem.removeprefix("music-")] = _path(folder, relative)
        return banners

    def _ensure(self, pack: PackInfo, ctx: JobContext) -> Path:
        with self._pack_lock:
            _check_cancel(ctx)
            folder = self.core.packs.ensure(
                pack,
                progress=lambda done, total: ctx.progress(done, total, f"Downloading {pack.name}"),
                cancel=lambda: ctx.cancelled,
            )
            _check_cancel(ctx)
            return folder

    def load_album(self, album: Album, ctx: JobContext) -> Album:
        _check_cancel(ctx)
        pack = album.pack
        if pack is None:
            return album
        if (
            (any(not t.role for t in album.tracks) or pack.meta.get("track_count") == 0)
            and not album.cached
            and self.core.packs.cached(pack.id) is None
        ):
            return album
        folder = self._ensure(pack, ctx)
        data = _manifest(pack, folder)
        tracks = _tracks(data["tracks"])
        cover = album.cover
        if cover is None and data.get("cover"):
            cover = _verified(folder, data["cover"], data["files"])
        return replace(
            album,
            tracks=tracks,
            track_count=len(tracks),
            cover=cover,
            cached=True,
            game=data.get("game", album.game),
            year=data.get("year", album.year),
            platform=data.get("platform", album.platform),
            series=data.get("series", album.series),
        )

    def game_state(self) -> GameState:
        install = self.core.installs.current()
        owned = _registry(install) if install else {}
        tracks = tuple(_originals(install, owned)) if install else ()
        pools = {role: tuple(track.title for track in tracks if track.role == role) for role in ROLES}
        local = Album(
            YOUR_MUSIC,
            "Your music",
            "Your music",
            platform="EDOPro",
            tracks=tracks,
            track_count=len(tracks),
            cached=True,
            install_root=install.root if install else None,
        )
        assignments = {}
        for role, titles in pools.items():
            managed = owned.get(role)
            has_file = managed and any(track.path == f"{_BGM}/{role}/{managed.filename}" for track in tracks)
            assignments[role] = managed.title if has_file else (titles[0] if titles else "Original")
        flags = []
        if install:
            try:
                flags = _MUSIC_FLAG.findall(_path(install.root, _CONF).read_text(encoding="utf-8-sig", errors="replace"))
            except FileNotFoundError:
                pass
        return GameState(
            install,
            local,
            assignments,
            bool(flags and flags[-1] == "0"),
            bool(owned),
            pools,
            managed_roles=tuple(owned),
            sources={k: [v.album_id, v.track_id] for k, v in owned.items() if v.album_id and v.track_id},
            managed_paths={k: f"{_BGM}/{k}/{v.filename}" for k, v in owned.items()},
        )

    def _source(self, album: Album, track: Track, ctx: JobContext) -> tuple[Path, Track]:
        _check_cancel(ctx)
        if album.pack and not track.role:
            pack = album.pack
            folder = self._ensure(pack, ctx)
            data = _manifest(pack, folder)
            full = next((item for item in _tracks(data["tracks"]) if item.id == track.id), None)
            if full is None:
                raise ValueError("The selected track is missing from the downloaded pack.")
            return _verified(folder, full.path, data["files"]), full
        install = self.core.installs.current()
        if not install or install.root != album.install_root:
            raise ValueError("The selected game folder changed. Select the track again.")
        # Resolve by the discovered ID so a stale selection cannot read arbitrary game files.
        local = next((item for item in _originals(install, _registry(install)) if item.id == track.id), None)
        if local is None:
            raise ValueError("The selected track is no longer in this game folder.")
        parent, name = local.path.rsplit("/", 1)
        path = _path(install.root, parent) / name
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(install.root.resolve()):
            raise ValueError("The selected music file moved outside this game folder.")
        return path, replace(local, title=track.title)

    def preview(self, album: Album, track: Track, ctx: JobContext) -> tuple[Path, bool]:
        """Return the preview source and whether the full pack is available offline."""
        _check_cancel(ctx)
        pack = album.pack
        if not track.role and pack and pack.preview and album.tracks and not album.cached:
            preview_id = pack.meta.get("preview_track", album.tracks[0].id)
            if track.id == preview_id and self.core.packs.cached(pack.id) is None:
                # PackService exposes one lightweight cached media fetcher. Give the clip its
                # own cache key so it never replaces the game's thumbnail.
                clip_info = replace(pack, id=f"{pack.id}-audio-preview", thumbnail=pack.preview)
                try:
                    clip = self.core.packs.thumbnail(clip_info)
                except (OSError, ValueError, RuntimeError):
                    clip = None
                if clip:
                    if (checksum := pack.meta.get("preview_sha256")) and _digest(clip) != checksum:
                        raise ValueError("Music preview verification failed.")
                    _check_cancel(ctx)
                    return clip, False
        return self._source(album, track, ctx)[0], pack is not None and not track.role

    def _require_closed(self, install: Install | None, confirmed: bool) -> None:
        current = self.core.installs.current()
        if not current or not install or current.root != install.root:
            raise ValueError("Select the EDOPro folder again before changing its music.")
        state = self.core.installs.run_state()
        if state is RunState.RUNNING:
            raise GameRunningError("Close EDOPro before changing its music.")
        if state is not RunState.CLOSED and not confirmed:
            raise ConfirmClosedError("Confirm that EDOPro is closed before changing its music.")

    @contextmanager
    def _edit(self, install: Install, confirmed: bool, ctx: JobContext):
        # The storage lock protects publication. Serialize reading our ownership record through
        # commit, too, so concurrent EDOPal processes never lose another role's assignment.
        with file_lock(self.core.paths.data / "music.lock"):
            _check_cancel(ctx)
            self._require_closed(install, confirmed)
            yield

    def assign(
        self, album: Album, track: Track, role: str, install: Install, ctx: JobContext, *, confirmed_closed: bool = False
    ) -> GameState:
        return self._apply({role: (album, track)}, install, ctx, confirmed_closed, f"Assign {track.title}")

    def restore(self, install, ctx, *, role=None, confirmed_closed=False):
        roles = (role,) if role is not None else ROLES
        return self._apply(dict.fromkeys(roles), install, ctx, confirmed_closed, "Restore Original Music")

    def apply_setup(self, refs, albums, install, ctx, *, confirmed_closed=False):
        """Resolve every source before publishing the entire setup in one changeset."""
        library = {album.id: album for album in albums}
        choices = dict.fromkeys(ROLES)
        self._require_closed(install, confirmed_closed)
        for role, (album_id, track_id) in refs.items():
            album = library.get(album_id)
            track = next((t for t in album.tracks if t.id == track_id), None) if album else None
            if track is None and album and album.pack:
                album = self.load_album(album, ctx)
                track = next((t for t in album.tracks if t.id == track_id), None)
            if not album or not track:
                raise ValueError(f"The saved track for {ROLES.get(role, role)} is unavailable.")
            choices[role] = (album, track)
        return self._apply(choices, install, ctx, confirmed_closed, "Apply Music Setup")

    def _apply(self, choices, install, ctx, confirmed, title):
        self._require_closed(install, confirmed)
        desired = {}
        for role, selection in choices.items():
            if role not in ROLES:
                raise ValueError("Choose one of the five music roles.")
            desired[role] = None
            if selection:
                album, track = selection
                source, track = self._source(album, track, ctx)
                slug = _slug(album.id.removeprefix("music-"))
                filename = f"edopal-{slug}-{_slug(track.id)}{source.suffix.lower()}"
                desired[role] = (
                    source,
                    _Owned(filename, track.title, _digest(source), album.id, track.id),
                    track.role == role,
                )

        conflicts = []
        with self._edit(install, confirmed, ctx):
            owned = _registry(install)
            initial = dict(owned)
            copies, removals = {}, []
            for role, choice in desired.items():
                previous = owned.get(role)
                base = f"{_BGM}/{role}/"
                old_rel = base + previous.filename if previous else None
                old = None
                unchanged = False
                if old_rel:
                    try:
                        old = _path(install.root, old_rel)
                        unchanged = old.is_file() and _digest(old) == previous.sha256
                        if old.exists() and not unchanged:
                            conflicts.append(old_rel)
                    except ValueError:
                        conflicts.append(old_rel)
                if choice and choice[2] and choice[0] == old:
                    continue
                owned.pop(role, None)
                if choice and not choice[2]:
                    source, item, _ = choice
                    relative = base + item.filename
                    target = _path(install.root, relative)
                    if target.parent.is_dir() and any(
                        p.name.casefold() == item.filename.casefold() and p.name != item.filename
                        for p in target.parent.iterdir()
                    ):
                        raise FileExistsError(f"A track already uses the name {item.filename}.")
                    if target.exists() and (old_rel != relative or not unchanged):
                        raise FileExistsError(f"An existing track already uses the name {item.filename}.")
                    if old_rel != relative or not unchanged or previous.sha256 != item.sha256:
                        copies[relative] = source
                    owned[role] = item
                if unchanged and (role not in owned or owned[role].filename != previous.filename):
                    removals.append(old_rel)
            if copies or removals or owned != initial:
                _check_cancel(ctx)
                self._require_closed(install, confirmed)
                with self.core.storage.changeset(install, "music", title, confirmed_closed=confirmed) as changes:
                    for relative, source in copies.items():
                        changes.copy_in(source, relative)
                    for relative in removals:
                        changes.remove(relative)
                    _persist(changes, owned)
        return replace(self.game_state(), conflicts=tuple(conflicts))

    def enable_music(self, install: Install, ctx: JobContext, *, confirmed_closed: bool = False) -> GameState:
        with self._edit(install, confirmed_closed, ctx):
            _path(install.root, _CONF)
            with self.core.storage.changeset(
                install, "music-settings", "Turn on EDOPro music", confirmed_closed=confirmed_closed
            ) as changes:
                changes.set_conf(_CONF, "enable_music", "1")
        return self.game_state()
