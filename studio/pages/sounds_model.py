"""Worker operations and in-memory choices for the Sounds page."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event

import config
import sounds
from content.cache import PackCache
from content.catalogue import Catalogue, CatalogueSource, PackState, PackStatus
from content.download import Progress, download_pack
from content.manifest import Pack
from music import settings as music_settings
from sounds.archive import SavedSet
from sounds.sets import InstalledCue
from sounds.settings import _ENABLE, SoundSettings
from studio.pages.music_sounds_controls import can_resume, check_game, partial_archives

EVENT_KEYS = tuple(event.stem for event in sounds.EVENTS)
EVENT_COUNT = len(EVENT_KEYS)
NEW_MIX = "Custom mix"


@dataclass(frozen=True)
class Source:
    key: str
    sound_set: sounds.SoundSet
    category: str


@dataclass(frozen=True)
class Snapshot:
    rows: tuple[InstalledCue, ...]
    sound_set: sounds.SoundSet
    settings: SoundSettings
    undo: bool
    restore: bool
    files: dict[str, tuple[tuple[Path, int, int, int], ...]]

    def matches(self, draft: sounds.SoundSet) -> bool:
        for key, clip in draft.assignments.items():
            if clip is None:
                if key in self.files:
                    return False
            else:
                installed = self.sound_set.assignments.get(key)
                if not installed or installed.sha256 != clip.sha256:
                    return False
        return True


@dataclass(frozen=True)
class Downloadable:
    status: PackStatus
    sound_set: sounds.SoundSet | None = None
    error: str = ""
    paused: bool = False

    @property
    def pack(self) -> Pack:
        return self.status.pack


@dataclass(frozen=True)
class OnlineSets:
    catalogue: Catalogue
    packs: tuple[Downloadable, ...]


class InstallationUnavailable(OSError):
    """The game folder itself cannot be opened."""


class FilesChanged(Exception):
    """The player needs a fresh look at the files before applying a choice."""


def library_for(config_path: str, explicit: bool) -> sounds.SoundLibrary:
    return sounds.SoundLibrary(Path(config_path).resolve().parent / "sounds" if explicit else None)


def scan(game: Path, config_path: str) -> Snapshot:
    try:
        rows = sounds.scan_installation(game)
    except OSError as exc:
        # A damaged cue should not change the installation indicator for every page.
        try:
            next(game.iterdir(), None)
        except OSError as folder_error:
            raise InstallationUnavailable(str(folder_error)) from exc
        raise
    files = {row.key: file_stamps(row.paths) for row in rows if row.paths}
    return Snapshot(
        rows,
        sounds.installed_set(rows),
        sounds.sound_settings(game),
        sounds.can_undo(game, config_path),
        sounds.can_restore(game, config_path),
        files,
    )


def file_stamps(paths: Iterable[Path]) -> tuple[tuple[Path, int, int, int], ...]:
    stamps = []
    for path in paths:
        info = path.stat()
        stamps.append((path, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return tuple(stamps)


def write_game(
    game: Path,
    config_path: str,
    action: str,
    draft: sounds.SoundSet,
    snapshot: Snapshot,
    *,
    confirmed_closed: bool = False,
) -> None:
    check_game(game, confirmed_closed)
    if action == "apply":
        current = {row.key: row for row in sounds.scan_installation(game)}
        for key in draft.assignments:
            row = current.get(key)
            clip = row.clip if row else None
            prior = snapshot.sound_set.assignments.get(key)
            if file_stamps(row.paths if row else ()) != snapshot.files.get(key, ()) or (
                clip and clip.sha256
            ) != (prior and prior.sha256):
                raise FilesChanged
        # The game may have been opened during the scan.
        check_game(game, confirmed_closed)
        sounds.apply_set(game, config_path, draft)
    elif action == "undo":
        sounds.undo(game, config_path)
    elif action == "restore":
        sounds.restore(game, config_path)
    else:
        try:
            sounds.set_sound_enabled(game, action == "enable")
        except music_settings.GameProcessUnknown:
            if not confirmed_closed:
                raise
            # The engine checked again. Use its atomic writer for this confirmed switch.
            music_settings.write_audio_enabled(game, action == "enable", _ENABLE, "enable_sound")


def coverage(sound_set: sounds.SoundSet) -> tuple[int, int]:
    playable = sum(sound_set.assignments.get(key) is not None for key in EVENT_KEYS)
    silent = sum(key in sound_set.assignments for key in EVENT_KEYS) - playable
    return playable, silent


def installed_name(snapshot: Snapshot, sources: Iterable[Source]) -> str:
    installed = {row.key: row.clip.sha256 if row.clip else "" for row in snapshot.rows if row.paths}
    if not installed:
        return "No sounds installed"
    for source in sources:
        if source.category == "Installed":
            continue
        expected = {key: clip.sha256 for key, clip in source.sound_set.assignments.items() if clip}
        if installed == expected:
            return source.sound_set.name
    return "Installed mix"


def choice_for(
    key: str, draft: sounds.SoundSet, snapshot: Snapshot | None, names: dict[str, str]
) -> tuple[str, float | None, sounds.Clip | None]:
    if key in draft.assignments:
        clip = draft.assignments[key]
        return (names.get(key, draft.name), clip.duration, clip) if clip else ("Silent", None, None)
    if snapshot is not None:
        clip = snapshot.sound_set.assignments.get(key)
        if clip:
            return "Keep installed sound", clip.duration, clip
        if key in snapshot.files:
            return "Keep unreadable sound", None, None
        return "No sound installed", None, None
    return "Keep game sound", None, None


def chants_for(draft: sounds.SoundSet, sources: Iterable[Source]) -> tuple[str, ...]:
    keys = set(draft.assignments)
    for source in sources:
        keys.update(source.sound_set.assignments)
    return tuple(sorted(keys - set(EVENT_KEYS)))


def event_description(key: str) -> str:
    optional = {
        "token": "A Token is summoned.",
        "reveal": "A card is revealed.",
        "directattack": "A monster attacks the opponent directly.",
    }
    if key in optional:
        return f"{optional[key]} Playback varies by EDOPro version."
    return sounds.event_for(key).description


def display_text(value: str) -> str:
    return " ".join(value.split())


def pack_audio(cache: PackCache, pack: Pack, *, exact: bool = True) -> sounds.SoundSet:
    with cache.use_pack(pack.id, exact=pack if exact else None) as path:
        if path is None:
            raise ValueError("The downloaded copy could not be verified. Download it again.")
        sound_set = sounds.load_pack(path, kind=pack.kind)
    if not exact:
        return sound_set
    credits = sound_set.credits
    return replace(
        sound_set,
        name=pack.title,
        credits=sounds.Credits(
            credits.author or pack.author,
            credits.license or pack.license,
            credits.attribution or pack.attribution,
        ),
    )


def online_sets(cache: PackCache, version: str, catalogue: Catalogue) -> OnlineSets:
    packs = []
    partials = partial_archives(cache)
    for status in catalogue.list_packs(cache, app_version=version, kind="sound-effects"):
        sound_set = None
        error = ""
        if status.state in (PackState.DOWNLOADED, PackState.UPDATE_AVAILABLE):
            try:
                sound_set = pack_audio(
                    cache, status.pack, exact=status.state is PackState.DOWNLOADED
                )
            except (OSError, ValueError):
                if status.state is PackState.DOWNLOADED:
                    error = "This download could not be opened. Try downloading it again."
        packs.append(Downloadable(status, sound_set, error, can_resume(status, partials)))
    return OnlineSets(catalogue, tuple(packs))


async def get_pack(
    cache: PackCache, pack: Pack, version: str, report: Callable[[Progress], None], cancel: Event
) -> Downloadable:
    await download_pack(cache, pack, app_version=version, progress=report, cancel=cancel)
    # Download verifies the exact archive. Open it under the cache's use lock.
    return Downloadable(PackStatus(pack, PackState.DOWNLOADED, None), pack_audio(cache, pack))


def catalogue_message(online: OnlineSets) -> str:
    if online.catalogue.source is CatalogueSource.LIVE:
        return "" if online.packs else "There are no downloadable sound sets in the catalog yet."
    if online.packs:
        return "The catalog is offline. Showing the last available list."
    return "The catalog is offline. Built-in and saved sounds are still available."


def saved_sources(rows: tuple[SavedSet, ...]) -> tuple[Source, ...]:
    return tuple(Source(row.id, row.sound_set, "Saved") for row in rows if row.sound_set)


def import_message(exc: BaseException, *, clip: bool) -> str:
    if clip:
        if isinstance(exc, OSError):
            return "Could not read that audio file. Check that it still exists and try again."
        return "Could not import that sound. Choose a supported audio file and try again."
    if isinstance(exc, OSError):
        return (
            "Could not open or save that set. Check the file and your available space, then retry."
        )
    return (
        "This file isn’t a supported sound set. Ask for a fresh export from "
        + config.APP_NAME
        + "."
    )
