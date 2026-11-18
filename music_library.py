"""Offline soundtrack library and reversible EDOPro playlist selection."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from config import user_data_dir
from state_lock import locked

ART = Path(__file__).resolve().parent / "assets" / "music"
FORMATS = frozenset({".flac", ".ogg", ".mp3", ".wav"})
SCENES = {"deck": "Deck editor", "duel": "Duels"}
LOCAL = "local"


@dataclass(frozen=True)
class Game:
    id: str
    title: str
    platform: str
    year: int
    aliases: tuple[str, ...]
    cover_page: str
    fan_cover: bool = False
    region: str = ""


@dataclass(frozen=True)
class Track:
    game: str
    title: str
    path: Path
    scene: str
    in_playlist: bool = False


def catalog() -> dict[str, Game]:
    games = {LOCAL: Game(LOCAL, "EDOPro playlist", "On this installation", 0, (), "")}
    for row in json.loads((ART / "catalog.json").read_text(encoding="utf-8")):
        item = Game(
            row["id"],
            row["title"],
            row["platform"],
            row["year"],
            tuple(row["aliases"]),
            row["cover_page"],
            "fanart" in row["cover_description"].casefold(),
            row.get("cover_region", ""),
        )
        games[item.id] = item
    return games


def library_root() -> Path:
    return Path(user_data_dir()) / "soundtracks"


def _scene(scene: str) -> str:
    if scene not in SCENES:
        raise ValueError("Choose Deck editor or Duels.")
    return scene


def _sound_path(game: Path, *parts: str) -> Path:
    root = game.resolve(strict=True)
    result = root.joinpath("sound", *parts)
    if not result.resolve().is_relative_to(root):
        raise OSError("The selected soundtrack folder points outside this EDOPro installation.")
    return result


def _folders(game: Path, scene: str):
    yield _sound_path(game, "BGM", _scene(scene))
    if scene == "duel":
        # EDOPro includes loose BGM files in the regular duel playlist.
        yield _sound_path(game, "BGM")


def _state(game: Path, scene: str) -> Path:
    return _sound_path(game, ".edopro-hd-sync", _scene(scene))


def _audio(folder: Path):
    if folder.is_dir():
        for item in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            if item.is_file() and item.suffix.lower() in FORMATS:
                yield item


def _title(path: Path) -> str:
    return path.stem.replace("_", " ")


def _identify(name: str, games: dict[str, Game]) -> str:
    plain = re.sub(r"[^a-z0-9]", "", name.casefold())
    matches = [
        (len(alias), game.id)
        for game in games.values()
        for alias in (*game.aliases, game.title)
        if re.sub(r"[^a-z0-9]", "", alias.casefold()) in plain
    ]
    return max(matches, default=(0, LOCAL))[1]


def _record(state: Path, filename: str = "selection.json") -> dict:
    try:
        value = json.loads((state / filename).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(value.get(key), str) for key in ("name", "game", "title", "digest")
    ):
        raise ValueError(
            "The saved soundtrack selection is unreadable. Your original playlist is in the saved folder."
        )
    ext = Path(value["name"]).suffix
    expected = f"HD Sync - {value['game']} - {value['digest'][:16]}{ext}"
    if (
        not re.fullmatch(r"[a-z0-9]+", value["game"])
        or not re.fullmatch(r"[a-f0-9]{64}", value["digest"])
        or ext not in FORMATS
        or value["name"] != expected
    ):
        raise ValueError("Cannot read the saved soundtrack selection.")
    return value


def tracks(game: Path, scene: str, *, root: Path | None = None) -> list[Track]:
    """List the selected installation's tracks and the user's per-game music library."""
    lookup = catalog()
    root = root or library_root()
    scene = _scene(scene)
    output = []
    state = _state(game, scene)
    selected = _record(state)
    for base in _folders(game, scene):
        for active, directory in ((True, base), (False, state / "saved" / base.name)):
            if not directory.resolve().is_relative_to(game.resolve()):
                raise OSError("The saved playlist points outside this EDOPro installation.")
            for path in _audio(directory):
                if active and selected.get("name") == path.name and base.name == scene:
                    output.append(Track(selected["game"], selected["title"], path, scene, True))
                else:
                    key = _identify(path.name, lookup)
                    output.append(Track(key, _title(path), path, scene, active))
    for key in lookup:
        for path in _audio(root / key / scene):
            output.append(Track(key, _title(path), path, scene))
    if scene == "duel":
        for path in _audio(ART.parent / "extras" / "sound" / "BGM" / "duel"):
            output.append(Track(LOCAL, _title(path), path, scene))
    # One row for the selected recording even when both the game and library hold a copy.
    unique = {}
    for entry in output:
        identity = (
            entry.game,
            entry.title.casefold(),
            entry.path.suffix.lower(),
            entry.path.stat().st_size,
        )
        unique.setdefault(identity, entry)
    return list(unique.values())


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _valid_audio(path: Path) -> None:
    if not path.is_file() or path.suffix.lower() not in FORMATS:
        raise ValueError("Choose a FLAC, Ogg Vorbis, MP3 or WAV audio file.")
    with path.open("rb") as source:
        header = source.read(12)
    extension = path.suffix.lower()
    headers = {".flac": b"fLaC", ".ogg": b"OggS", ".wav": (b"RIFF", b"RF64")}
    if extension in headers and not header.startswith(headers[extension]):
        raise ValueError(f"{path.name} does not contain {extension[1:].upper()} audio.")
    if extension == ".mp3" and not (
        header.startswith(b"ID3")
        or (len(header) > 1 and header[0] == 255 and header[1] & 224 == 224)
    ):
        raise ValueError(f"{path.name} does not appear to contain MP3 audio.")
    try:
        import miniaudio
    except ImportError:
        return
    try:
        info = miniaudio.get_file_info(str(path))
    except miniaudio.MiniaudioError as exc:
        raise ValueError(
            f"Cannot decode {path.name}. Choose FLAC, Ogg Vorbis, MP3 or WAV audio."
        ) from exc
    if not info.num_frames or not info.sample_rate:
        raise ValueError(f"{path.name} has no playable audio.")


def _copy_new(source: Path, destination: Path) -> None:
    """Publish a complete file without replacing an existing one."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=".import-", delete=False
    ) as staged:
        temp = Path(staged.name)
    try:
        shutil.copyfile(source, temp)
        if destination.exists():
            raise FileExistsError(destination)
        # Both paths are in the locked library/installation on the same volume.
        os.rename(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def playlist_lock(game: Path, scene: str):
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
    if (state / "pending.json").exists():
        raise OSError(
            "An earlier soundtrack change was interrupted. Restore the duel playlist first."
        )
    if _record(state):
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


def import_track(source: Path, key: str, scene: str, *, root: Path | None = None) -> Path:
    return store_track(source, key, scene, root=root)[0]


def store_track(
    source: Path, key: str, scene: str, *, root: Path | None = None
) -> tuple[Path, bool]:
    """Store one recording and report whether a new file was added."""
    if key not in catalog():
        raise ValueError("Choose a game from the library.")
    _valid_audio(source)
    storage = (root or library_root()).resolve()
    base = storage / key / _scene(scene)
    if not base.resolve().is_relative_to(storage):
        raise OSError("The target folder points outside your soundtrack library.")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source.stem).strip().rstrip(".")[:160] or "Track"
    if re.fullmatch(r"(?:AUX|PRN|CON|NUL|COM[1-9]|LPT[1-9])", stem, re.I):
        stem = "_" + stem
    with locked(base):
        out = base / (stem + source.suffix.lower())
        index = 2
        while out.exists():
            if source.samefile(out) or _digest(source) == _digest(out):
                return out, False
            out = base / f"{stem} ({index}){source.suffix.lower()}"
            index += 1
        _copy_new(source, out)
        return out, True


def can_restore(game: Path, scene: str) -> bool:
    state = _state(game, scene)
    return (
        (state / "pending.json").is_file()
        or (state / "selection.json").is_file()
        or any(any(_audio(state / "saved" / folder.name)) for folder in _folders(game, scene))
    )


def _write_pending(state: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=state, prefix=".select-", delete=False
    ) as tmp:
        temp = Path(tmp.name)
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temp.replace(state / "pending.json")
    finally:
        temp.unlink(missing_ok=True)


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


def _clear_retired(changes: list[tuple[Path, Path]], state: Path) -> None:
    for _, moved in changes:
        if moved.parent == state and moved.name.startswith(".retiring-"):
            with contextlib.suppress(OSError):
                moved.unlink()


def use_track(game: Path, track: Track) -> None:
    """Make one track the scene's playlist. Park every pre-existing track for Restore."""
    scene = _scene(track.scene)
    if track.game not in catalog():
        raise ValueError("Unknown soundtrack.")
    _valid_audio(track.path)
    state = _state(game, scene)
    main = _sound_path(game, "BGM", scene)
    state_saved = state / "saved"
    if not state_saved.resolve().is_relative_to(game.resolve()):
        raise OSError("The backup folder points outside this installation.")
    with playlist_lock(game, scene):
        pending = state / "pending.json"
        if pending.exists():
            raise OSError(
                "The last soundtrack change was interrupted. Choose Restore playlist, then try again."
            )
        main.mkdir(parents=True, exist_ok=True)
        digest = _digest(track.path)
        old = _record(state)
        name = f"HD Sync - {track.game} - {digest[:16]}{track.path.suffix.lower()}"
        active = main / name
        if (
            old.get("digest") == digest
            and active.is_file()
            and _digest(active) == digest
            and sum(1 for folder in _folders(game, scene) for _ in _audio(folder)) == 1
        ):
            return
        fd, tmpname = tempfile.mkstemp(dir=state, prefix=".prepared-")
        os.close(fd)
        temp = Path(tmpname)
        changes = []
        try:
            # Stage first, since the source itself might be the track about to be moved.
            shutil.copyfile(track.path, temp)
            if _digest(temp) != digest:
                raise OSError("The source changed during copying. Please try again.")
            # Until this journal becomes the committed selection, Restore can identify
            # both possible managed tracks after a process exit or power interruption.
            _write_pending(
                state, {"name": name, "digest": digest, "game": track.game, "title": track.title}
            )
            _retire_current(main, state, old, changes)
            for folder in _folders(game, scene):
                backup = state_saved / folder.name
                if not backup.resolve().is_relative_to(game.resolve()):
                    raise OSError("The backup folder points outside this installation.")
                for path in list(_audio(folder)):
                    if path.is_symlink():
                        raise OSError(f"Cannot move linked track {path.name}.")
                    _move(path, backup / path.name, changes)
            _move(temp, active, changes)
            pending.replace(state / "selection.json")
        except BaseException:
            _rollback(changes)
            pending.unlink(missing_ok=True)
            raise
        finally:
            temp.unlink(missing_ok=True)
        _clear_retired(changes, state)


def restore_playlist(game: Path, scene: str) -> None:
    scene = _scene(scene)
    state = _state(game, scene)
    with playlist_lock(game, scene):
        changes = []
        try:
            _retire_current(_sound_path(game, "BGM", scene), state, _record(state), changes)
            _retire_current(
                _sound_path(game, "BGM", scene), state, _record(state, "pending.json"), changes
            )
            for folder in _folders(game, scene):
                backup = state / "saved" / folder.name
                if not backup.resolve().is_relative_to(game.resolve()):
                    raise OSError("The backup folder points outside this installation.")
                for old in list(_audio(backup)):
                    _move(old, folder / old.name, changes)
            (state / "selection.json").unlink(missing_ok=True)
            (state / "pending.json").unlink(missing_ok=True)
        except BaseException:
            _rollback(changes)
            raise
        _clear_retired(changes, state)


_ENABLE_MUSIC = re.compile(
    rb"(?m)^(?:\xef\xbb\xbf)?[ \t]*enable_music[ \t]*=[ \t]*([01])(?=[ \t]*(?:[#;][^\r\n]*)?\r?$)"
)


def music_enabled(game: Path) -> bool | None:
    try:
        conf = (game / "config" / "system.conf").read_bytes()
    except OSError:
        return None
    matches = list(_ENABLE_MUSIC.finditer(conf))
    return matches[-1][1] == b"1" if matches else None


def music_disabled(game: Path) -> bool:
    return music_enabled(game) is False


def _game_running(game: Path) -> bool:
    """Match the executable path so an unrelated portable installation can remain open."""
    root = game.resolve()
    if sys.platform.startswith("linux"):
        executables = []
        for proc in Path("/proc").iterdir():
            if proc.name.isdigit():
                with contextlib.suppress(OSError):
                    executables.append((proc / "exe").readlink())
    else:
        if sys.platform == "win32":
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
                "Get-Process -Name EDOPro,ygopro -ErrorAction SilentlyContinue | "
                "ForEach-Object {if ($_.Path) {$_.Path} else {$_.ProcessName}}; exit 0",
            ]
        else:
            command = ["ps", "-axo", "comm="]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError(
                "Could not check whether EDOPro is open. Use EDOPro’s audio settings instead."
            ) from exc
        executables = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return any(
        exe.stem.casefold() in {"edopro", "ygopro"}
        and (not exe.is_absolute() or exe.resolve().is_relative_to(root))
        for exe in executables
    )


def set_music_enabled(game: Path, enabled: bool) -> None:
    """Change only the existing enable_music bit, preserving EDOPro's volume and file bytes."""
    _set_audio_enabled(game, enabled, _ENABLE_MUSIC, "music", "enable_music")


def _set_audio_enabled(
    game: Path, enabled: bool, pattern: re.Pattern[bytes], label: str, key: str
) -> None:
    root = game.resolve(strict=True)
    conf = root / "config" / "system.conf"
    if not conf.resolve().is_relative_to(root) or conf.is_symlink():
        raise OSError("The EDOPro settings file points outside the selected installation.")
    if _game_running(root):
        raise OSError(f"Close this EDOPro installation before changing its {label} setting.")
    with locked(_sound_path(game, ".edopro-hd-sync"), ".audio-settings.lock"):
        original = conf.read_bytes()
        matches = list(pattern.finditer(original))
        if not matches or matches[-1][1] not in (b"0", b"1"):
            raise ValueError(
                f"This configuration has no recognized {key} switch. Use EDOPro’s audio settings."
            )
        match = matches[-1]
        value = b"1" if enabled else b"0"
        if match[1] == value:
            return
        with tempfile.NamedTemporaryFile(
            dir=conf.parent, prefix=".audio-setting-", delete=False
        ) as temp:
            staged = Path(temp.name)
        try:
            staged.write_bytes(original[: match.start(1)] + value + original[match.end(1) :])
            shutil.copymode(conf, staged)
            if conf.read_bytes() != original:
                raise OSError("EDOPro settings changed during the update. Please try again.")
            staged.replace(conf)
        finally:
            staged.unlink(missing_ok=True)
