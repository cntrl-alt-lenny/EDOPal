"""Read-only discovery and validation of scene metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from content.media import Origin
from music.audio import FORMATS, _audio, _digest, _scene
from music.catalogue import ART, ID, LOCAL, Game, catalog, identify, library_root
from music.digest_cache import browsing_digests
from music.packs import pack_media


@dataclass(frozen=True)
class Track:
    game: str
    title: str
    path: Path
    scene: str
    in_playlist: bool = False
    source: str = "personal"
    digest: str = ""
    origin: Origin | None = None


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


def describe(track: Track, digest: str) -> dict:
    return {
        **({"origin": asdict(track.origin)} if track.origin else {}),
        "name": f"HD Sync - {track.game} - {digest[:16]}{track.path.suffix.lower()}",
        "digest": digest,
        "game": track.game,
        "title": track.title,
    }


def record_track(row: dict, path: Path, scene: str) -> Track:
    origin = Origin(**row["origin"]) if "origin" in row else None
    return Track(row["game"], row["title"], path, scene, digest=row["digest"], origin=origin)


def validate_records(values: object) -> list[dict]:
    if not isinstance(values, list):
        raise ValueError("Cannot read the saved soundtrack selection.")
    for row in values:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(k), str) for k in ("name", "digest", "game", "title")
        ):
            raise ValueError("Incomplete soundtrack selection.")
        if "origin" in row:
            try:
                Origin(**row["origin"])
            except TypeError as exc:
                raise ValueError("Invalid soundtrack credit record.") from exc
        ext = Path(row["name"]).suffix
        if (
            not ID.fullmatch(row["game"])
            or not re.fullmatch(r"[a-f0-9]{64}", row["digest"])
            or ext not in FORMATS
            or row["name"] != f"HD Sync - {row['game']} - {row['digest'][:16]}{ext}"
        ):
            raise ValueError("Invalid soundtrack selection.")
    return values


def records(state: Path, filename: str = "selection.json") -> list[dict]:
    data = read_json(state / filename)
    if not data:
        return []
    values = validate_records(data.get("tracks", [data]))
    if not values and data.get("cleared") is not True:
        raise ValueError("Empty soundtrack selection.")
    return values


def active_tracks(
    game: Path, scene: str, games: dict[str, Game], *, digest_file: Callable[[Path], str] = _digest
) -> list[Track]:
    try:
        selected = records(_state(game, scene))
    except (ValueError, OSError):
        selected = []
    ordered = {row["name"]: i for i, row in enumerate(selected)}
    rows = []
    for folder in _folders(game, scene):
        for path in _audio(folder):
            index = ordered.get(path.name)
            digest = digest_file(path)
            if folder.name == scene and index is not None and selected[index]["digest"] == digest:
                record = selected[index]
                rows.append(
                    replace(record_track(record, path, scene), in_playlist=True, source="installed")
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


def physical_tracks(
    game: Path,
    scene: str,
    root: Path,
    games: dict[str, Game],
    *,
    digest_file: Callable[[Path], str] | None = None,
) -> Iterator[Track]:
    """Browse credited recordings, or use the caller's verification policy."""
    source = browsing_digests(root) if digest_file is None else nullcontext(digest_file)
    with source as digest:
        yield from _physical_tracks(game, scene, root, games, digest)


def _physical_tracks(
    game: Path, scene: str, root: Path, games: dict[str, Game], digest_file: Callable[[Path], str]
) -> Iterator[Track]:
    output = active_tracks(game, scene, games, digest_file=digest_file)
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
                    digest_file(path),
                )
            )
    for key in games:
        for path in _audio(root / key / scene):
            output.append(
                Track(key, _title(path), path, scene, False, "personal", digest_file(path))
            )
    for row in pack_media(root):
        item = row.item
        if item.role == "track" and scene in item.scenes and row.path.is_file():
            output.append(
                Track(
                    item.game,
                    item.title,
                    row.path,
                    scene,
                    False,
                    "pack",
                    digest_file(row.path),
                    row.origin,
                )
            )
    for path in _audio(ART.parent / "extras" / "sound" / "BGM" / scene):
        output.append(Track(LOCAL, _title(path), path, scene, False, "bundled", digest_file(path)))
    # Identical personal audio can identify an otherwise anonymous installed recording.
    # Keep its playable live path and active flag. Explicit album assignments are independent.
    personal = {row.digest: row for row in output if row.source in {"personal", "pack"}}
    credits = {(row.game, row.digest): row.origin for row in output if row.origin}
    for row in output:
        known = personal.get(row.digest)
        if known and (row.game == LOCAL or row.title.startswith("HD Sync - ")):
            row = replace(row, game=known.game, title=known.title)
        yield replace(row, origin=row.origin or credits.get((row.game, row.digest)))


def scan_scene(
    game: Path,
    scene: str,
    root: Path,
    games: dict[str, Game],
    *,
    digest_file: Callable[[Path], str] = _digest,
) -> list[Track]:
    unique = {}
    for row in physical_tracks(game, scene, root, games, digest_file=digest_file):
        unique.setdefault((row.game, row.digest), row)
    return list(unique.values())


def tracks(game: Path, scene: str, *, root: Path | None = None) -> list[Track]:
    root = root or library_root()
    with browsing_digests(root) as digest:
        return scan_scene(game, _scene(scene), root, catalog(root=root), digest_file=digest)
