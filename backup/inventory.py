"""Inventory personal files by metadata. Only small configuration/index files need opening."""

import io
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from music_library import FORMATS

from . import preferences
from .core import (
    CATEGORIES,
    MAX_FILES,
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


def category(name: str) -> str:
    portable(name)
    parts = name.split("/")
    if len(parts) == 2 and parts[0] == "preferences" and parts[1] in preferences.KEYS:
        return "preferences"
    if name == INDEX or (
        name.startswith(("studio/sleeves/", "studio/themes/"))
        and Path(name).suffix.lower()
        in (IMAGES | {".json"} if parts[1] == "sleeves" else {".json"})
    ):
        return "studio"
    for key, folder in FOLDERS.items():
        if name.startswith(folder + "/") and Path(name).suffix.lower() in SUFFIXES[key]:
            if key == "puzzles" and folded(parts[2]) == "canon collection":
                break
            if key == "audio" and (len(parts) != 5 or parts[3] not in {"deck", "duel"}):
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
    tracks = []
    for key, folder in folders.items():
        if key not in selected and not (
            "studio" in selected and key in {"sleeves", "themes", "audio"}
        ):
            continue
        excluded = managed_puzzles(install) if key == "puzzles" else set()
        for file in walk(install, folder, excluded, cancel):
            suffixes = SUFFIXES.get(key, IMAGES | {".json"} if key == "sleeves" else {".json"})
            if Path(file.name).suffix.lower() not in suffixes:
                continue
            if key == "audio":
                parts = file.name.split("/")
                if len(parts) != 5 or parts[3] not in {"deck", "duel"}:
                    continue
                tracks.append("/".join(parts[2:]))
            category(file.name)
            if key != "audio" or key in selected:
                result.append(file)
            if max(len(result), len(tracks)) > MAX_FILES:
                raise ValueError("Too many personal files")
    if "studio" in selected:
        index = install.target(INDEX)
        if index.exists():
            stored = json.loads(read_small(index, preferences.LIMIT))
            if not isinstance(stored, list) or len(stored) > MAX_FILES:
                raise ValueError("Invalid soundtrack index")
            for track in stored:
                if not isinstance(track, str) or category("studio/soundtracks/" + track) != "audio":
                    raise ValueError("Invalid soundtrack index entry")
            tracks.extend(stored)
        if tracks or index.exists():
            data = json.dumps(sorted(set(tracks)), ensure_ascii=False).encode()
            if len(data) > preferences.LIMIT:
                raise ValueError("Soundtrack index too large")
            result.append(Source(INDEX, len(data), data))
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


def hashes(install: Installation, categories: Iterable[str], cancel: Cancel) -> dict[str, str]:
    return {
        file.name: file.copy_to(install, None, cancel)[0]
        for file in sources(install, categories, cancel)
    }
