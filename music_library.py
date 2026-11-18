"""Offline soundtrack library and reversible EDOPro playlist selection."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from contextlib import AbstractContextManager
from pathlib import Path

from music.audio import (
    FORMATS,
    _audio,
    _copy_new,
    _digest,
    _valid_audio,
    import_track,
    store_track,
)
from music.catalogue import (
    ART,
    LOCAL,
    SCENES,
    Game,
    atomic_json,
    catalog,
    create_game,
    library_root,
)
from music.catalogue import (
    identify as _identify,
)
from music.discovery import (
    Track,
    _folders,
    _sound_path,
    _state,
    active_tracks,
    backup_folder,
    describe,
    read_json,
    records,
    scan_scene,
    tracks,
    validate_records,
)
from music.settings import (
    _ENABLE_MUSIC,
    _game_running,
    music_disabled,
    music_enabled,
    write_audio_enabled,
)
from state_lock import locked

__all__ = [
    "ART",
    "FORMATS",
    "LOCAL",
    "SCENES",
    "Game",
    "Track",
    "_identify",
    "catalog",
    "create_game",
    "library_root",
    "import_track",
    "store_track",
    "tracks",
    "set_playlist",
    "playlist",
    "use_track",
    "add_to_playlist",
    "remove_from_playlist",
    "use_game",
    "can_restore",
    "can_undo",
    "restore_playlist",
    "undo_playlist",
    "music_enabled",
    "music_disabled",
    "set_music_enabled",
    "original_duel_track_path",
    "playlist_lock",
]


def playlist_lock(game: Path, scene: str) -> AbstractContextManager[None]:
    return locked(_state(game, scene))


def original_duel_track_path(game: Path, filename: str) -> Path:
    """Resolve a normal duel playlist file while the caller holds playlist_lock."""
    if (
        Path(filename).name != filename
        or any(ch in filename for ch in "/\\:")
        or Path(filename).suffix.lower() not in FORMATS
    ):
        raise ValueError("Invalid duel soundtrack filename.")
    state = _state(game, "duel")
    live = _sound_path(game, "BGM", "duel", filename)
    saved = _sound_path(game, ".edopro-hd-sync", "duel", "saved", "duel", filename)
    if (state / "pending.json").exists() or (state / "restoring.json").exists():
        raise OSError(
            "An earlier soundtrack change was interrupted. Restore the duel playlist first."
        )
    if (state / "selection.json").exists():
        if live.exists():
            raise OSError(
                f"{filename} was added to the active duel playlist. Restore the playlist before editing the original."
            )
        return saved
    if saved.exists():
        raise OSError(
            "The original playlist was partly restored. Finish restoring it before changing the music."
        )
    return live


def can_restore(game: Path, scene: str) -> bool:
    state = _state(game, scene)
    return (
        (state / "restoring.json").is_file()
        or (state / "pending.json").is_file()
        or (state / "selection.json").is_file()
        or any(any(_audio(state / "saved" / folder.name)) for folder in _folders(game, scene))
    )


def _move(source: Path, destination: Path, changes: list[tuple[Path, Path]]) -> None:
    if destination.exists():
        raise FileExistsError(
            f"Two tracks share the name {destination.name}. Rename one before changing this playlist."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    changes.append((source, destination))


def _rollback(changes: list[tuple[Path, Path]]) -> None:
    for original, moved in reversed(changes):
        if moved.exists() and not original.exists():
            moved.rename(original)


def _retire_current(folder: Path, state: Path, record: dict, changes: list) -> None:
    # Only move the exact unchanged file that this tool installed. A replaced file is user data.
    if not record or Path(record["name"]).name != record["name"]:
        return
    active = folder / record["name"]
    if active.is_file() and _digest(active) == record["digest"]:
        fd, name = tempfile.mkstemp(dir=state, prefix=".retiring-")
        os.close(fd)
        retired = Path(name)
        retired.unlink()
        _move(active, retired, changes)


def _clear_staging(state: Path) -> None:
    """After commit, reclaim transient copies including those from an interrupted change."""
    for path in state.iterdir():
        if not re.fullmatch(r"\.(?:prepared|retiring)-[a-z0-9_]{8}", path.name):
            continue
        with contextlib.suppress(OSError):
            if path.is_file():
                path.unlink()
            elif path.resolve() == path:
                for staged in path.iterdir():
                    if (
                        re.fullmatch(r"HD Sync - [a-z0-9]+ - [a-f0-9]{16}", staged.stem)
                        and staged.suffix in FORMATS
                    ):
                        staged.unlink()
                path.rmdir()


def set_music_enabled(game: Path, enabled: bool) -> None:
    _set_audio_enabled(game, enabled, _ENABLE_MUSIC, "music", "enable_music")


def _set_audio_enabled(
    game: Path, enabled: bool, pattern: re.Pattern[bytes], label: str, key: str
) -> None:
    """Shared by the music and sound-effects switches; both need the game closed."""
    if _game_running(game):
        raise OSError(f"Close this EDOPro installation before changing its {label} setting.")
    write_audio_enabled(game, enabled, pattern, key)


def _write_pending(state: Path, value: dict) -> None:
    atomic_json(state / "pending.json", value)


def _history(game: Path, scene: str) -> Path:
    return _sound_path(game, ".edopro-hd-sync", scene, "undo")


def _remember(game: Path, scene: str, rows: Iterable[Track]) -> list[dict]:
    history = _history(game, scene)
    result = []
    for row in rows:
        digest = _digest(row.path)
        record = describe(row, digest)
        copy = history / record["name"]
        if not copy.exists():
            _copy_new(row.path, copy, digest)
        if _digest(copy) != digest:
            raise OSError("The previous playlist changed during copying.")
        result.append(record)
    return result


def _prune_history(game: Path, scene: str, previous: list[dict]) -> None:
    keep = {row["name"] for row in previous}
    for path in _audio(_history(game, scene)):
        if path.name not in keep:
            with contextlib.suppress(OSError):
                path.unlink()


def _publish(
    game: Path, scene: str, chosen: Iterable[Track], games: dict[str, Game], *, undo: bool = False
) -> None:
    state = _state(game, scene)
    main = _sound_path(game, "BGM", scene)
    pending = state / "pending.json"
    if pending.exists() or (state / "restoring.json").exists():
        raise OSError("The last soundtrack change was interrupted. Choose Restore playlist first.")
    try:
        old = records(state)
    except ValueError as exc:
        raise ValueError("The saved selection is unreadable. Restore the playlist first.") from exc
    current = active_tracks(game, scene, games)
    desired: list[dict] = []
    seen = set()
    changes: list[tuple[Path, Path]] = []
    with tempfile.TemporaryDirectory(dir=state, prefix=".prepared-") as temporary:
        for track in chosen:
            if track.game not in games:
                raise ValueError("Choose a game from the library.")
            _valid_audio(track.path)
            digest = _digest(track.path)
            if digest in seen:
                continue
            seen.add(digest)
            row = describe(track, digest)
            staged = Path(temporary) / row["name"]
            shutil.copyfile(track.path, staged)
            if _digest(staged) != digest:
                raise OSError("The source changed during copying. Please try again.")
            desired.append(row)
        if not desired:
            raise ValueError("Keep at least one track or restore the original playlist.")
        if old == desired and [row["digest"] for row in old] == [t.digest for t in current]:
            return
        previous = _remember(game, scene, current) if old and not undo else []
        old_files = {(row["name"], row["digest"]) for row in old}
        # Recovery must distinguish a pre-existing identically named file from a published copy.
        originals = [
            t.path.name
            for t in current
            if t.path.parent == main and (t.path.name, t.digest) not in old_files
        ]
        selection = {"tracks": desired, "original_names": originals}
        if not undo:
            selection["previous"] = previous  # Empty denotes the original EDOPro folders.
        _write_pending(state, selection)
        try:
            for record in old:
                _retire_current(main, state, record, changes)
            for folder in _folders(game, scene):
                backup = backup_folder(game, scene, folder)
                for path in list(_audio(folder)):
                    if path.is_symlink():
                        raise OSError(f"Cannot move linked track {path.name}.")
                    _move(path, backup / path.name, changes)
            for record in desired:
                _move(Path(temporary) / record["name"], main / record["name"], changes)
            pending.replace(state / "selection.json")
        except BaseException:
            _rollback(changes)
            pending.unlink(missing_ok=True)
            raise
    _clear_staging(state)
    (state / "restored.json").unlink(missing_ok=True)
    _prune_history(game, scene, previous)


def set_playlist(
    game: Path, scene: str, chosen: Iterable[Track], *, root: Path | None = None
) -> None:
    """Replace this scene with an ordered set. Tracks can come from any scene."""
    with playlist_lock(game, scene):
        _publish(game, scene, chosen, catalog(root=root))


def playlist(game: Path, scene: str, *, root: Path | None = None) -> list[Track]:
    """The scene's ordered active rows, including EDOPro's native playlist before any edit."""
    return active_tracks(game, scene, catalog(root=root))


def use_track(game: Path, track: Track, *, root: Path | None = None) -> None:
    set_playlist(game, track.scene, [track], root=root)


def add_to_playlist(game: Path, track: Track, *, root: Path | None = None) -> None:
    with playlist_lock(game, track.scene):
        games = catalog(root=root)
        current = active_tracks(game, track.scene, games)
        digest = track.digest or _digest(track.path)
        if any(row.digest == digest for row in current):
            return
        _publish(game, track.scene, [*current, track], games)


def remove_from_playlist(game: Path, track: Track, *, root: Path | None = None) -> None:
    with playlist_lock(game, track.scene):
        games = catalog(root=root)
        current = active_tracks(game, track.scene, games)
        digest = track.digest or _digest(track.path)
        kept = [row for row in current if row.digest != digest]
        if len(kept) != len(current):
            _publish(game, track.scene, kept, games)


def use_game(
    game: Path, scene: str, key: str, *, root: Path | None = None, all_scenes: bool = False
) -> None:
    """Select the game's cues for this scene, or its complete album across scenes."""
    with playlist_lock(game, scene):
        games = catalog(root=root)
        source_scenes = SCENES if all_scenes else (scene,)
        chosen = []
        for source in source_scenes:
            rows = scan_scene(game, source, root or library_root(), games)
            chosen.extend(
                sorted(
                    (row for row in rows if row.game == key), key=lambda row: row.title.casefold()
                )
            )
        _publish(game, scene, chosen, games)


def _restore(game: Path, scene: str, *, remember: bool) -> None:
    state = _state(game, scene)
    if not can_restore(game, scene):
        return
    changes = []
    restoring = state / "restoring.json"
    resumed = restoring.exists()
    saved = []
    try:
        if resumed:
            # All managed tracks have been retired. Some originals may already be live.
            try:
                saved = validate_records(read_json(restoring).get("previous", []))
            except (OSError, ValueError):
                saved = []
        else:
            saved = _retire_selection(game, scene, changes, remember=remember)
            atomic_json(restoring, {"previous": saved})
        for folder in _folders(game, scene):
            for path in list(_audio(backup_folder(game, scene, folder))):
                _move(path, folder / path.name, changes)
    except BaseException:
        _rollback(changes)
        if not resumed:
            restoring.unlink(missing_ok=True)
        raise
    # From here on a retry only finishes metadata cleanup, never retires a restored original.
    if saved:
        atomic_json(state / "restored.json", {"previous": saved})
    else:
        (state / "restored.json").unlink(missing_ok=True)
    (state / "pending.json").unlink(missing_ok=True)
    (state / "selection.json").unlink(missing_ok=True)
    restoring.unlink()
    _clear_staging(state)
    _prune_history(game, scene, saved)


def _retire_selection(
    game: Path, scene: str, changes: list[tuple[Path, Path]], *, remember: bool
) -> list[dict]:
    state = _state(game, scene)
    main = _sound_path(game, "BGM", scene)
    damaged = False
    managed = []
    for filename in ("selection.json", "pending.json"):
        try:
            candidates = records(state, filename)
            if filename == "pending.json" and candidates:
                originals = read_json(state / filename).get("original_names", [])
                if not isinstance(originals, list):
                    raise ValueError("Invalid pending soundtrack selection.")
                candidates = [
                    r
                    for r in candidates
                    if r["name"] not in originals
                    or (backup_folder(game, scene, main) / r["name"]).exists()
                ]
            managed.extend(candidates)
        except (ValueError, OSError):
            damaged = True
    previous = []
    if remember and not damaged and not (state / "pending.json").exists():
        for row in managed:
            path = main / row["name"]
            if path.is_file() and _digest(path) == row["digest"]:
                previous.append(Track(row["game"], row["title"], path, scene))
    saved = _remember(game, scene, previous)
    for record in managed:
        _retire_current(main, state, record, changes)
    if damaged:
        # A damaged journal cannot prove ownership. Preserve plausible managed files in recovery.
        for path in list(_audio(main)):
            match = re.fullmatch(r"HD Sync - [a-z0-9]+ - ([a-f0-9]{16})", path.stem)
            if match and _digest(path).startswith(match[1]):
                recovery = _sound_path(game, ".edopro-hd-sync", scene, "recovered")
                recovery.mkdir(exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    dir=recovery, suffix=path.suffix, prefix=path.stem + "-", delete=False
                ) as f:
                    destination = Path(f.name)
                destination.unlink()
                _move(path, destination, changes)
    return saved


def restore_playlist(game: Path, scene: str) -> None:
    with playlist_lock(game, scene):
        _restore(game, scene, remember=True)


def _previous(state: Path) -> list[dict] | None:
    if (state / "pending.json").exists() or (state / "restoring.json").exists():
        return None
    path = state / "selection.json"
    if not path.exists():
        path = state / "restored.json"
    data = read_json(path)
    if "previous" in data:
        return validate_records(data["previous"])
    if "name" in data:
        validate_records([data])
        return []
    return None


def can_undo(game: Path, scene: str) -> bool:
    try:
        return _previous(_state(game, scene)) is not None
    except (ValueError, OSError):
        return False


def undo_playlist(game: Path, scene: str, *, root: Path | None = None) -> None:
    """Revert the most recent edit or restore in this scene."""
    with playlist_lock(game, scene):
        previous = _previous(_state(game, scene))
        if previous is None:
            raise ValueError("There is no previous playlist to undo.")
        if not previous:
            _restore(game, scene, remember=False)
            return
        history = _history(game, scene)
        chosen = []
        for row in previous:
            path = history / row["name"]
            if _digest(path) != row["digest"]:
                raise OSError("The undo copy has changed. Restore the original playlist instead.")
            chosen.append(Track(row["game"], row["title"], path, scene))
        _publish(game, scene, chosen, catalog(root=root), undo=True)
