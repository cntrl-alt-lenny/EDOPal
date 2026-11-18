"""Inventory personal files and resolve editable library assets from bounded metadata."""

import io
import json
import os
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from music.audio import FORMATS
from music.catalogue import CUSTOM, SCENES

from . import collection, preferences
from .core import (
    CATEGORIES,
    MAX_FILES,
    MAX_TOTAL,
    Cancel,
    Installation,
    check,
    folded,
    linked,
    portable,
    read_small,
    transfer,
)

INDEX = "studio/soundtracks/index.json"
CONF = "game/config/system.conf"
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
FOLDERS = {
    "decks": "game/deck",
    "replays": "game/replay",
    "puzzles": "game/puzzles",
    "screenshots": "game/screenshots",
    "audio": "studio/soundtracks",
}
SUFFIXES = {
    "decks": {".ydk"},
    "replays": {".yrp", ".yrpx"},
    "puzzles": {".lua"},
    "screenshots": IMAGES,
    "audio": FORMATS,
}


def album_metadata(parts: list[str]) -> bool:
    return (
        len(parts) == 4
        and parts[:2] == ["studio", "soundtracks"]
        and bool(CUSTOM.fullmatch(parts[2]))
        and parts[3] == "game.json"
    )


def track_path(parts: list[str]) -> bool:
    return (
        len(parts) == 5
        and parts[:2] == ["studio", "soundtracks"]
        and parts[3] in SCENES
        and Path(parts[4]).suffix.lower() in FORMATS
    )


def validate_index(tracks: object) -> None:
    if not isinstance(tracks, list) or len(tracks) > MAX_FILES:
        raise ValueError("Invalid soundtrack index")
    for track in tracks:
        if not isinstance(track, str) or not track_path(("studio/soundtracks/" + track).split("/")):
            raise ValueError("Invalid soundtrack index entry")
        portable("studio/soundtracks/" + track)


def file_limit(name: str) -> int:
    if name == INDEX or album_metadata(name.split("/")):
        return preferences.LIMIT
    return collection.size_limit(name)


def category(name: str, selected: Collection[str] = CATEGORIES) -> str:
    portable(name)
    parts = name.split("/")
    if len(parts) == 2 and parts[0] == "preferences" and parts[1] in preferences.KEYS:
        return "preferences"
    if album_metadata(parts):
        # A named album belongs to the collection as well as a standalone audio export.
        return "studio" if "studio" in selected else "audio"
    if track_path(parts):
        return "audio"
    if (
        name == INDEX
        or collection.asset_digest(name)
        or (
            name.startswith(("studio/sleeves/", "studio/themes/"))
            and Path(name).suffix.lower()
            in (IMAGES | {".json"} if parts[1] == "sleeves" else {".json"})
        )
    ):
        return "studio"
    for key, folder in FOLDERS.items():
        if name.startswith(folder + "/") and Path(name).suffix.lower() in SUFFIXES[key]:
            if key == "puzzles" and folded(parts[2]) == "canon collection":
                break
            if key == "audio":
                break
            return key
    raise ValueError(f"Path outside portable categories: {name}")


def managed_puzzles(install: Installation) -> set[str]:
    excluded = {"puzzles/canon collection"}
    for conf in ("configs.json", "user_configs.json"):
        path = install.target("game/config/" + conf)
        if path.exists():
            cfg = json.loads(read_small(path, preferences.LIMIT).decode("utf-8-sig"))
            repos = cfg.get("repos", []) if isinstance(cfg, dict) else None
            if not isinstance(repos, list) or not all(isinstance(r, dict) for r in repos):
                raise ValueError(f"Invalid puzzle repository configuration: {conf}")
            for repo in repos:
                raw = repo.get("repo_path")
                if isinstance(raw, str):
                    # Only compare configured locations. Resolving an absolute
                    # network share here would contact it during offline inventory.
                    repo_path = Path(os.path.normpath(install.game / raw.replace("\\", "/")))
                    if repo_path.is_relative_to(install.game):
                        excluded.add(folded(repo_path.relative_to(install.game).as_posix()))
    return excluded


def managed(name: str, excluded: set[str]) -> bool:
    rel = folded(name.removeprefix("game/"))
    return any(p == "." or rel == p or rel.startswith(p + "/") for p in excluded)


@dataclass(frozen=True)
class Source:
    name: str
    size: int
    content: bytes | None = None
    modified_at: int | None = None

    def open(self, install: Installation) -> BinaryIO:
        if self.content is not None:
            return io.BytesIO(self.content)
        path = install.target(self.name)
        if not path.is_file():
            raise ValueError(f"Source is no longer a regular file: {self.name}")
        return path.open("rb")

    def copy_to(
        self, install: Installation, output: BinaryIO | None, cancel: Cancel
    ) -> tuple[str, int | None]:
        with self.open(install) as stream:
            initial = os.fstat(stream.fileno()) if self.content is None else None
            size, digest = transfer(stream, output, self.size, cancel)
            if size != self.size:
                raise ValueError(f"File changed during read: {self.name}")
            expected = collection.asset_digest(self.name)
            if expected and expected != digest:
                raise ValueError(f"Collection asset checksum mismatch: {self.name}")
            if initial is not None:
                current = install.target(self.name).stat()
                if (
                    current.st_mtime_ns != initial.st_mtime_ns
                    or current.st_size != size
                    or not os.path.samestat(initial, current)
                ):
                    raise ValueError(f"File changed during read: {self.name}")
            return digest, int(initial.st_mtime) if initial is not None else self.modified_at


@dataclass(frozen=True)
class Total:
    count: int
    bytes: int


def selection(categories: Iterable[str]) -> frozenset[str]:
    result = frozenset(categories)
    if result.difference(CATEGORIES):
        raise ValueError("Unknown backup category")
    return result


def walk(
    install: Installation, folder: str, excluded: set[str], cancel: Cancel
) -> Iterator[Source]:
    parent = install.target(folder)
    if not parent.exists() or (
        folder == "game/puzzles" and (managed(folder, excluded) or (parent / ".git").exists())
    ):
        return
    stack = [(parent, folder)]
    while stack:
        directory, prefix = stack.pop()
        with os.scandir(directory) as children:
            for child in children:
                check(cancel)
                name = prefix + "/" + child.name
                path = Path(child.path)
                if (
                    child.name.startswith(".")
                    or linked(path)
                    or (excluded and managed(name, excluded))
                ):
                    continue
                if child.is_dir(follow_symlinks=False):
                    if name in {
                        "studio/themes/images",
                        "studio/sleeves/originals",
                        "studio/sleeves/card-art",
                        "studio/sleeves/thumbnails",
                        "studio/soundtracks/pack-media",
                    }:
                        continue
                    if folder == "game/puzzles" and (path / ".git").exists():
                        continue
                    stack.append((path, name))
                elif child.is_file(follow_symlinks=False):
                    yield Source(name, child.stat(follow_symlinks=False).st_size)


def sources(
    install: Installation, categories: Iterable[str], cancel: Cancel = None
) -> list[Source]:
    selected = selection(categories)
    folders = dict(FOLDERS)
    if "studio" in selected:
        folders.update(sleeves="studio/sleeves", themes="studio/themes")
    result = []
    tracks: set[str] = set()
    required: set[str] = set()
    for key, folder in folders.items():
        if key not in selected and not (
            "studio" in selected and key in {"sleeves", "themes", "audio"}
        ):
            continue
        excluded = managed_puzzles(install) if key == "puzzles" else set()
        for file in walk(install, folder, excluded, cancel):
            suffixes = SUFFIXES.get(key, IMAGES | {".json"} if key == "sleeves" else {".json"})
            if key == "audio":
                parts = file.name.split("/")
                if track_path(parts):
                    tracks.add("/".join(parts[2:]))
                elif not album_metadata(parts):
                    continue
            elif Path(file.name).suffix.lower() not in suffixes:
                continue
            cat = category(file.name, selected)
            if collection.recipe(file.name) or file.name in collection.CATALOGUES:
                # Keep the exact recipe used for dependency discovery, even if the editor
                # saves it again while the archive is being written.
                path = install.target(file.name)
                data = read_small(path, collection.size_limit(file.name))
                required.update(collection.references(file.name, data))
                file = Source(file.name, len(data), data, int(path.stat().st_mtime))
            if cat in selected:
                result.append(file)
            if max(len(result), len(tracks), len(required)) > MAX_FILES:
                raise ValueError("Too many personal files")
    if "studio" in selected:
        for name in sorted(required - {f.name for f in result}):
            check(cancel)
            path = install.target(name)
            if not path.is_file():
                raise ValueError(f"Missing collection asset: {name}")
            result.append(Source(name, path.stat().st_size))
        index = install.target(INDEX)
        if index.exists():
            stored = json.loads(read_small(index, preferences.LIMIT))
            validate_index(stored)
            tracks.update(stored)
            if len(tracks) > MAX_FILES:
                raise ValueError("Too many soundtrack files")
        if tracks or index.exists():
            encoded = bytearray()
            for part in json.JSONEncoder(ensure_ascii=False).iterencode(sorted(tracks)):
                block = part.encode()
                if len(encoded) + len(block) > preferences.LIMIT:
                    raise ValueError("Soundtrack index too large")
                encoded.extend(block)
            result.append(Source(INDEX, len(encoded), bytes(encoded)))
    if "preferences" in selected:
        path = install.target(CONF)
        if path.exists():
            modified_at = int(path.stat().st_mtime)
            for key, value in preferences.values(read_small(path, preferences.LIMIT)).items():
                result.append(Source("preferences/" + key, len(value), value, modified_at))
    if len(result) > MAX_FILES:
        raise ValueError("Too many personal files")
    return sorted(result, key=lambda f: f.name)


def inventory(install: Installation, *, cancel: Cancel = None) -> dict[str, Total]:
    counts = dict.fromkeys(CATEGORIES, 0)
    sizes = counts.copy()
    for file in sources(install, CATEGORIES, cancel):
        cat = category(file.name)
        counts[cat] += 1
        sizes[cat] += file.size
    return {cat: Total(counts[cat], sizes[cat]) for cat in CATEGORIES}


def bounded_files(install: Installation, categories: Iterable[str], cancel: Cancel) -> list[Source]:
    files = sources(install, categories, cancel)
    if sum(f.size for f in files) > MAX_TOTAL or any(f.size > file_limit(f.name) for f in files):
        raise ValueError("Selection exceeds the archive size limit")
    return files


def hashes(install: Installation, categories: Iterable[str], cancel: Cancel) -> dict[str, str]:
    return {
        file.name: file.copy_to(install, None, cancel)[0]
        for file in bounded_files(install, categories, cancel)
    }
