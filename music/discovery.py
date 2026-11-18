"""Read-only discovery and validation of scene metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from music.audio import FORMATS, _audio, _digest, _scene
from music.catalogue import ART, ID, LOCAL, Game, catalog, identify, library_root


@dataclass(frozen=True)
class Track:
    game: str
    title: str
    path: Path
    scene: str
    in_playlist: bool = False
    source: str = "personal"
    digest: str = ""


def _sound_path(game: Path, *parts: str) -> Path:
    root = game.resolve(strict=True)
    result = root.joinpath("sound", *parts)
    if not result.resolve().is_relative_to(root):
        raise OSError("The selected soundtrack folder points outside this EDOPro installation.")
    return result


def _folders(game: Path, scene: str) -> Iterator[Path]:
    yield _sound_path(game, "BGM", _scene(scene))
    if scene == "duel":
        # EDOPro includes loose BGM files in the regular duel playlist.
        yield _sound_path(game, "BGM")


def _state(game: Path, scene: str) -> Path:
    return _sound_path(game, ".edopro-hd-sync", _scene(scene))


def _title(path: Path) -> str:
    return path.stem.replace("_", " ")


def backup_folder(game: Path, scene: str, base: Path) -> Path:
    return _sound_path(game, ".edopro-hd-sync", scene, "saved", base.name)


def read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except RecursionError as exc:
        raise ValueError(f"Unreadable soundtrack record {path.name}.") from exc
    if not isinstance(obj, dict) or not obj:
        raise ValueError(f"Unreadable soundtrack record {path.name}.")
    return obj


def describe(track: Track, digest: str) -> dict[str, str]:
    return {
        "name": f"HD Sync - {track.game} - {digest[:16]}{track.path.suffix.lower()}",
        "digest": digest,
        "game": track.game,
        "title": track.title,
    }


def validate_records(values: object) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise ValueError("Cannot read the saved soundtrack selection.")
    for row in values:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(k), str) for k in ("name", "digest", "game", "title")
        ):
            raise ValueError("Incomplete soundtrack selection.")
        ext = Path(row["name"]).suffix
        if (
            not ID.fullmatch(row["game"])
            or not re.fullmatch(r"[a-f0-9]{64}", row["digest"])
            or ext not in FORMATS
            or row["name"] != f"HD Sync - {row['game']} - {row['digest'][:16]}{ext}"
        ):
            raise ValueError("Invalid soundtrack selection.")
    return values


def records(state: Path, filename: str = "selection.json") -> list[dict[str, str]]:
    data = read_json(state / filename)
    if not data:
        return []
    values = validate_records(data.get("tracks", [data]))
    if not values:
        raise ValueError("Empty soundtrack selection.")
    return values


def active_tracks(game: Path, scene: str, games: dict[str, Game]) -> list[Track]:
    try:
        selected = records(_state(game, scene))
    except (ValueError, OSError):
        selected = []
    ordered = {row["name"]: i for i, row in enumerate(selected)}
    rows = []
    for folder in _folders(game, scene):
        for path in _audio(folder):
            index = ordered.get(path.name)
            digest = _digest(path)
            if folder.name == scene and index is not None and selected[index]["digest"] == digest:
                record = selected[index]
                rows.append(
                    Track(record["game"], record["title"], path, scene, True, "installed", digest)
                )
            else:
                rows.append(
                    Track(
                        identify(path.name, games),
                        _title(path),
                        path,
                        scene,
                        True,
                        "installed",
                        digest,
                    )
                )
    return sorted(rows, key=lambda t: ordered.get(t.path.name, len(ordered)))


def physical_tracks(game: Path, scene: str, root: Path, games: dict[str, Game]) -> Iterator[Track]:
    output = active_tracks(game, scene, games)
    for base in _folders(game, scene):
        for path in _audio(backup_folder(game, scene, base)):
            output.append(
                Track(
                    identify(path.name, games),
                    _title(path),
                    path,
                    scene,
                    False,
                    "parked",
                    _digest(path),
                )
            )
    for key in games:
        for path in _audio(root / key / scene):
            output.append(Track(key, _title(path), path, scene, False, "personal", _digest(path)))
    for path in _audio(ART.parent / "extras" / "sound" / "BGM" / scene):
        output.append(Track(LOCAL, _title(path), path, scene, False, "bundled", _digest(path)))
    # Identical personal audio can identify an otherwise anonymous installed recording.
    # Keep its playable live path and active flag. Explicit album assignments are independent.
    personal = {row.digest: row for row in output if row.source == "personal"}
    for row in output:
        known = personal.get(row.digest)
        if known and (row.game == LOCAL or row.title.startswith("HD Sync - ")):
            row = replace(row, game=known.game, title=known.title)
        yield row


def scan_scene(game: Path, scene: str, root: Path, games: dict[str, Game]) -> list[Track]:
    unique = {}
    for row in physical_tracks(game, scene, root, games):
        unique.setdefault((row.game, row.digest), row)
    return list(unique.values())


def tracks(game: Path, scene: str, *, root: Path | None = None) -> list[Track]:
    root = root or library_root()
    return scan_scene(game, _scene(scene), root, catalog(root=root))
