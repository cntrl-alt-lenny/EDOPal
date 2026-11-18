"""Offline soundtrack library and reversible EDOPro playlist selection."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from music.audio import (
    FORMATS,
    _audio,
    _digest,
    import_track,
    store_track,
)
from music.audio import (
    validate_audio as _valid_audio,
)
from music.catalogue import (
    ART,
    LOCAL,
    SCENES,
    Game,
    catalog,
    create_game,
    library_root,
)
from music.catalogue import (
    identify as _identify,
)
from music.digest_cache import browsing_digests
from music.discovery import (
    Track,
    _folders,
    _sound_path,
    _state,
    active_tracks,
    describe,
    read_json,
    record_track,
    scan_scene,
    tracks,
    validate_records,
)
from music.originals import Originals, read_journal, read_selection, write_journal
from music.playlist_files import (
    _clear_staging,
    _history,
    _move,
    _prune_history,
    _remember,
    _retire_current,
    _rollback,
)
from music.settings import (
    _ENABLE_MUSIC,
    _game_running,
    game_process_status,
    is_game_running,
    music_disabled,
    music_enabled,
    write_audio_enabled,
)
from music.storage import copy_synced, ensure_directory, sync_directory
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
    "is_game_running",
    "game_process_status",
    "music_disabled",
    "set_music_enabled",
    "original_duel_track_path",
    "playlist_lock",
]


@contextmanager
def playlist_lock(game: Path, scene: str) -> Iterator[None]:
    state = _state(game, scene)
    ensure_directory(state)
    with locked(state):
        yield


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
    write_journal(state / "pending.json", value)


def _publish(
    game: Path,
    scene: str,
    chosen: Iterable[Track],
    games: dict[str, Game],
    *,
    undo: bool = False,
    clear: bool = False,
    expected: Sequence[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    state = _state(game, scene)
    main = _sound_path(game, "BGM", scene)
    pending = state / "pending.json"
    if pending.exists() or (state / "restoring.json").exists():
        raise OSError("The last soundtrack change was interrupted. Choose Restore playlist first.")
    originals = Originals(game, scene)
    old = []
    managed = (state / "selection.json").exists()
    if managed:
        _, old = read_selection(state, "selection.json", originals)
    elif can_restore(game, scene):
        raise OSError(f"The saved playlist has no selection record. Recovery files are in {state}.")
    originals.collect_parked()
    originals.verify_parked()
    current = active_tracks(game, scene, games)
    if expected is not None and list(expected) != [row.digest for row in current]:
        raise ValueError(
            "The playlist changed since it was opened. Reload the library before editing it."
        )
    desired: list[dict] = []
    seen = set()
    changes: list[tuple[Path, Path]] = []
    with tempfile.TemporaryDirectory(dir=state, prefix=".prepared-") as temporary:
        tracks_to_copy = list(chosen)
        for index, track in enumerate(tracks_to_copy):
            if progress:
                progress(index, len(tracks_to_copy))
            if track.game not in games:
                raise ValueError("Choose a game from the library.")
            _valid_audio(track.path)
            digest = _digest(track.path)
            if expected is not None and track.digest and track.digest != digest:
                raise ValueError(
                    "A recording changed since it was opened. Reload the library before editing it."
                )
            if digest in seen:
                continue
            seen.add(digest)
            row = describe(track, digest)
            staged = Path(temporary) / row["name"]
            copy_synced(track.path, staged)
            if _digest(staged) != digest:
                raise OSError("The source changed during copying. Please try again.")
            desired.append(row)
        if not desired and not clear:
            raise ValueError("Keep at least one track or restore the original playlist.")
        if (
            (old or (state / "selection.json").exists())
            and old == desired
            and [row["digest"] for row in old] == [t.digest for t in current]
        ):
            return
        previous = _remember(game, scene, current) if managed and not undo else []
        old_files = {(main / row["name"], row["digest"]) for row in old}
        for track in current:
            if (track.path, track.digest) not in old_files:
                originals.add_live(track.path, track.digest)
        selection = {"tracks": desired, "originals": originals.journal()}
        if managed:
            selection["prior_selection"] = _digest(state / "selection.json")
        if clear and not desired:
            selection["cleared"] = True
        if not undo:
            selection["previous"] = previous  # Empty denotes the original EDOPro folders.
            if managed and not current:
                selection["previous_cleared"] = True
        if progress:
            progress(len(tracks_to_copy), len(tracks_to_copy))
        _write_pending(state, selection)
        try:
            for record in old:
                _retire_current(main, state, record, changes)
            for track in current:
                if (track.path, track.digest) not in old_files:
                    _move(track.path, originals.parked(track.path), changes)
            originals.verify_parked()
            for record in desired:
                _move(Path(temporary) / record["name"], main / record["name"], changes)
            pending.replace(state / "selection.json")
        except BaseException:
            _rollback(changes)
            pending.unlink(missing_ok=True)
            sync_directory(state)
            raise
        sync_directory(state)
    _clear_staging(state)
    (state / "restored.json").unlink(missing_ok=True)
    _prune_history(game, scene, previous)


def set_playlist(
    game: Path,
    scene: str,
    chosen: Iterable[Track],
    *,
    root: Path | None = None,
    clear: bool = False,
    expected: Sequence[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Replace this scene with an ordered set. Tracks can come from any scene."""
    with playlist_lock(game, scene):
        _publish(
            game,
            scene,
            chosen,
            catalog(root=root),
            clear=clear,
            expected=expected,
            progress=progress,
        )


def playlist(game: Path, scene: str, *, root: Path | None = None) -> list[Track]:
    """The scene's ordered active rows, including EDOPro's native playlist before any edit."""
    root = root or library_root()
    with browsing_digests(root) as digest:
        return active_tracks(game, scene, catalog(root=root), digest_file=digest)


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


def _restore(
    game: Path, scene: str, *, remember: bool, checkpoint: Callable[[], None] | None = None
) -> None:
    state = _state(game, scene)
    if not can_restore(game, scene):
        return
    originals = Originals(game, scene)
    restoring = state / "restoring.json"
    main = _sound_path(game, "BGM", scene)
    if restoring.exists():
        journal = read_journal(restoring)
        originals.load(journal)
        managed = validate_records(journal.get("managed"))
        saved = validate_records(journal.get("previous"))
        if type(journal.get("previous_cleared")) is not bool:
            raise ValueError("Incomplete soundtrack restore record.")
        cleared = journal["previous_cleared"]
        for name in ("pending.json", "selection.json"):
            expected = journal["sources"].get(name)
            path = state / name
            if path.exists():
                if _digest(path) != expected:
                    raise OSError(f"The playlist recovery record {name} has changed.")
            elif expected and not journal.get("verified"):
                raise OSError(f"The playlist recovery record {name} is missing.")
    else:
        managed = []
        selection = {}
        sources = {}
        for filename in ("selection.json", "pending.json"):
            if (state / filename).exists():
                selection, rows = read_selection(state, filename, originals)
                managed.extend(rows)
                if filename == "pending.json" and selection.get("prior_selection") != sources.get(
                    "selection.json"
                ):
                    raise OSError(
                        "The selection record for the interrupted playlist is missing or changed."
                    )
                sources[filename] = _digest(state / filename)
        if not sources:
            raise OSError(f"The playlist recovery record is missing. Saved files are in {state}.")
        originals.collect_parked()
        moves = originals.restore_sources(main, managed)
        parked = {live for _, live in moves}
        previous = []
        remember = remember and not (state / "pending.json").exists()
        if remember:
            for row in managed:
                path = main / row["name"]
                if path in originals.expected and path not in parked:
                    continue
                if path.is_file() and _digest(path) == row["digest"]:
                    previous.append(record_track(row, path, scene))
        saved = _remember(game, scene, previous, checkpoint)
        cleared = remember and selection.get("cleared") is True
        journal = {
            "originals": originals.journal(),
            "managed": managed,
            "previous": saved,
            "previous_cleared": cleared,
            "sources": sources,
        }
        if checkpoint:
            checkpoint()
        write_journal(restoring, journal)

    for row in saved:
        path = _history(game, scene) / row["name"]
        if not path.is_file() or _digest(path) != row["digest"]:
            raise OSError(f"The undo copy {path.name} is missing or changed.")
    moves = originals.restore_sources(main, managed)
    parked = {live for _, live in moves}
    changes = []
    try:
        for row in managed:
            path = main / row["name"]
            if path not in originals.expected or path in parked:
                _retire_current(main, state, row, changes)
        for source, destination in moves:
            _move(source, destination, changes)
        for live, original in originals.expected.items():
            original.verify(live)
    except BaseException:
        _rollback(changes)
        raise
    if not journal.get("verified"):
        journal["verified"] = True
        write_journal(restoring, journal)
    if saved or cleared:
        write_journal(state / "restored.json", {"previous": saved, "previous_cleared": cleared})
    else:
        (state / "restored.json").unlink(missing_ok=True)
    (state / "pending.json").unlink(missing_ok=True)
    (state / "selection.json").unlink(missing_ok=True)
    sync_directory(state)
    restoring.unlink()
    sync_directory(state)
    _clear_staging(state)
    _prune_history(game, scene, saved)


def restore_playlist(
    game: Path, scene: str, *, checkpoint: Callable[[], None] | None = None
) -> None:
    with playlist_lock(game, scene):
        _restore(game, scene, remember=True, checkpoint=checkpoint)


def _previous(state: Path) -> list[dict] | None:
    if (state / "pending.json").exists() or (state / "restoring.json").exists():
        return None
    path = state / "selection.json"
    if not path.exists():
        path = state / "restored.json"
    data = read_json(path)
    if "checksum" in data:
        data = read_journal(path)
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


def undo_playlist(
    game: Path,
    scene: str,
    *,
    root: Path | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Revert the most recent edit or restore in this scene."""
    with playlist_lock(game, scene):
        state = _state(game, scene)
        previous = _previous(state)
        if previous is None:
            raise ValueError("There is no previous playlist to undo.")
        if not previous:
            record = state / "selection.json"
            if not record.exists():
                record = state / "restored.json"
            if read_json(record).get("previous_cleared") is True:
                _publish(game, scene, (), catalog(root=root), undo=True, clear=True)
                return
            _restore(game, scene, remember=False, checkpoint=checkpoint)
            return
        history = _history(game, scene)
        chosen = []
        for row in previous:
            if checkpoint:
                checkpoint()
            path = history / row["name"]
            if _digest(path) != row["digest"]:
                raise OSError("The undo copy has changed. Restore the original playlist instead.")
            chosen.append(record_track(row, path, scene))
        _publish(
            game,
            scene,
            chosen,
            catalog(root=root),
            undo=True,
            progress=(lambda *_: checkpoint()) if checkpoint else None,
        )
